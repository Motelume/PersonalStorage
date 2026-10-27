from pathlib import Path
import zipfile,re,json,sys,subprocess,os,hashlib,tempfile,threading,sqlite3,importlib.util
from xml.etree import ElementTree as ET
from . import ocr
from .common import linked,SafetyError

_OCR_SLOT=threading.BoundedSemaphore(1)

def signature():
    from .recognition import home
    cache=home()/'results.sqlite3'
    return ocr.signature()+(':enhanced-'+str(cache.stat().st_mtime_ns) if cache.exists() else '')

def _extract_local(path,limit=16000):
    p=Path(path); result={'name':p.name,'kind':'directory' if p.is_dir() else 'file','text':'','complete':False,'warnings':[]}
    try:
        if p.is_dir():
            result['members']=[str(x.relative_to(p)) for x in p.iterdir()][:80]
            result['warnings'].append('完整目录保持整体，未递归解析正文')
        elif p.suffix.lower() in __import__('personalstorage.extra_formats',fromlist=['FORMATS']).FORMATS:
            from .extra_formats import read
            return read(p,limit)
        elif p.suffix.lower() in ('.txt','.md','.csv','.json'):
            with p.open('rb') as f: raw=f.read(limit*4+1)
            result['text']=raw.decode('utf-8-sig',errors='replace')[:limit]
            result['complete']=len(raw)<=limit*4 and len(result['text'])<limit
        elif p.suffix.lower() in ocr.IMAGES or (p.suffix.lower()=='.pdf' and importlib.util.find_spec('pypdfium2') is not None):
            options=json.loads(os.environ['PERSONALSTORAGE_OCR_JOB_SETTINGS']) if os.environ.get('PERSONALSTORAGE_OCR_JOB_SETTINGS') else ocr.settings()
            return ocr.read_document(p,limit,options)
        elif p.suffix.lower()=='.pdf':
            from pypdf import PdfReader
            reader=PdfReader(p)
            text='\n'.join((page.extract_text() or '') for page in reader.pages[:5])
            result['text']=text[:limit]; result['complete']=len(reader.pages)<=5 and len(text)<=limit
        elif p.suffix.lower() in ('.docx','.pptx','.xlsx','.zip'):
            with zipfile.ZipFile(p) as z:
                infos=z.infolist(); result['members']=[x.filename for x in infos[:100]]
                if p.suffix.lower()=='.docx': names=['word/document.xml']
                elif p.suffix.lower()=='.pptx': names=sorted([x.filename for x in infos if re.fullmatch(r'ppt/slides/slide\d+\.xml',x.filename)],key=lambda s:int(re.search(r'slide(\d+)',s).group(1)))[:12]
                elif p.suffix.lower()=='.xlsx': names=[x.filename for x in infos if x.filename in ('xl/sharedStrings.xml','xl/workbook.xml')]+[x.filename for x in infos if x.filename.startswith('xl/worksheets/sheet')][:3]
                else: names=[]
                texts=[]
                for name in names:
                    info=z.getinfo(name)
                    if info.file_size>2*1024*1024: result['warnings'].append('XML超限未读取'); continue
                    data=z.read(name)
                    if b'<!DOCTYPE' in data or b'<!ENTITY' in data: raise ValueError('拒绝XML实体声明')
                    texts.append(' '.join(x.text or '' for x in ET.fromstring(data).iter() if x.tag.endswith('}t') or x.tag.endswith('}v')))
                result['text']='\n'.join(texts)[:limit]
                result['complete']=False
        else: result['warnings'].append('格式暂无只读解析器；不得仅凭扩展名分类')
    except Exception as e: result['warnings'].append(type(e).__name__+': '+str(e)[:120])
    return result

def _worker(p,limit,timeout,options=None):
    from .processes import run as run_process
    environment=dict(os.environ)
    if options is not None:environment['PERSONALSTORAGE_OCR_JOB_SETTINGS']=json.dumps(options)
    if getattr(sys,'frozen',False):
        with tempfile.TemporaryDirectory(prefix='personalstorage-extract-') as folder:
            output=Path(folder)/'extracted.json'
            done=run_process([sys.executable,'--extract-worker',str(p),str(limit),str(output)],check=False,env=environment,capture_output=True,timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if done.returncode or not output.is_file():raise ValueError('只读解析子进程失败')
            return json.loads(output.read_text(encoding='utf-8'))
    done=run_process([sys.executable,'-X','utf8','-m','personalstorage.extraction','--worker',str(p),str(limit)],check=False,env=environment,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding='utf-8',timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if done.returncode:raise ValueError('只读解析子进程失败')
    return json.loads(done.stdout)

def _version(path):
    value=path.stat();return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)

def _sha(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(4*1024*1024):value.update(block)
    return value.hexdigest()

def _trim(result,p,limit,hit=False):
    result={**result,'name':p.name,'text':result.get('text','')[:limit],'warnings':list(result.get('warnings',[]))}
    result['ocr']={**result.get('ocr',{}),'cache_hit':hit}
    if result.get('text') and len(result['text'])>=limit:
        result['complete']=False
    return result

def extract(path,limit=16000,timeout=None,*,force=False,options=None,use_cache=True):
    p=Path(path).absolute()
    limit=max(1,min(16000,limit))
    if p.is_file():
        from .recognition import home,cached
        if (home()/'results.sqlite3').exists():
            approved=cached(p)
            if approved is not None:return {**approved,'text':approved['text'][:limit]}
    candidate=p.suffix.lower() in ocr.IMAGES|{'.pdf'} and not p.is_dir()
    from .extra_formats import FORMATS
    if p.is_dir() or (not candidate and p.suffix.lower() not in {'.docx','.pptx','.xlsx','.zip'}|FORMATS):
        return _extract_local(p,limit)
    fallback={'name':p.name,'kind':'file','text':'','complete':False,'warnings':[]}
    try:
        if p.stat().st_size>128*1024*1024:
            fallback['warnings'].append('正文/包解析超过128MiB预算；仍可按明确任务上下文整体归档，完整性哈希不省略')
            return fallback
        if linked(p):raise SafetyError('拒绝链接或重解析点')
        if not candidate:return _worker(p,limit,timeout or 15)
        config=ocr.validate(options or ocr.settings());stamp=_version(p);content_hash=_sha(p)
        if _version(p)!=stamp:raise SafetyError('识别前文件发生变化，请重试')
        key=hashlib.sha256((content_hash+p.suffix.lower()+ocr.signature(config)).encode()).hexdigest();epoch=0
        if use_cache:
            try:
                cached,epoch=ocr.cache_get(key)
                if cached is not None and not force:return _trim(cached,p,limit,True)
            except (OSError,ValueError,sqlite3.Error):fallback['warnings'].append('本机识别缓存不可用，本次直接识别')
        # OCR sees an immutable generated snapshot, never a changing original.
        with _OCR_SLOT,tempfile.TemporaryDirectory(prefix='personalstorage-ocr-input-') as folder:
            snapshot=Path(folder)/('source'+p.suffix.lower());copied=hashlib.sha256()
            with p.open('rb') as source,snapshot.open('xb') as target:
                while block:=source.read(4*1024*1024):target.write(block);copied.update(block)
            if copied.hexdigest()!=content_hash or _version(p)!=stamp:raise SafetyError('创建识别快照时文件变化，请重试')
            result=_worker(snapshot,16000,timeout if timeout is not None else config['timeout'],config)
        if _version(p)!=stamp or _sha(p)!=content_hash:raise SafetyError('识别期间原文件变化，旧结果未采用')
        if options is None and config!=ocr.settings():raise SafetyError('识别配置已变化，请重新读取')
        if use_cache:
            try:ocr.cache_put(key,result,epoch)
            except (OSError,ValueError,sqlite3.Error):result.setdefault('warnings',[]).append('本次识别成功，但缓存保存失败')
        result.setdefault('warnings',[]).extend(fallback['warnings'])
        return _trim(result,p,limit)
    except subprocess.TimeoutExpired:
        fallback['warnings'].append('本地识别/解析超时，工作进程已停止；可调整超时后重试')
        if candidate:fallback['ocr']={'status':'timeout','cache_hit':False}
    except (OSError,ValueError,SafetyError) as e:
        fallback['warnings'].append(str(e)[:160])
        if candidate:fallback['ocr']={'status':'error','cache_hit':False}
    return fallback

if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='--worker': raise SystemExit(2)
    print(json.dumps(_extract_local(sys.argv[2],int(sys.argv[3])),ensure_ascii=False))
