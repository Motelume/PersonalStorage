from pathlib import Path
import zipfile,re,json,sys,subprocess,os
from xml.etree import ElementTree as ET

def _extract_local(path,limit=16000):
    p=Path(path); result={'name':p.name,'kind':'directory' if p.is_dir() else 'file','text':'','complete':False,'warnings':[]}
    try:
        if p.is_dir():
            result['members']=[str(x.relative_to(p)) for x in p.iterdir()][:80]
            result['warnings'].append('完整目录保持整体，未递归解析正文')
        elif p.suffix.lower() in ('.txt','.md','.csv','.json'):
            with p.open('rb') as f: raw=f.read(limit*4+1)
            result['text']=raw.decode('utf-8-sig',errors='replace')[:limit]
            result['complete']=len(raw)<=limit*4 and len(result['text'])<limit
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

def extract(path,limit=16000,timeout=15):
    p=Path(path).absolute()
    if p.is_dir() or p.suffix.lower() not in ('.pdf','.docx','.pptx','.xlsx','.zip'):
        return _extract_local(p,limit)
    fallback={'name':p.name,'kind':'file','text':'','complete':False,'warnings':[]}
    try:
        if p.stat().st_size>128*1024*1024:
            fallback['warnings'].append('正文/包解析超过128MiB预算；仍可按明确任务上下文整体归档，完整性哈希不省略')
            return fallback
        done=subprocess.run([sys.executable,'-X','utf8','-m','personalstorage.extraction','--worker',str(p),str(min(limit,16000))],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding='utf-8',timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if done.returncode: raise ValueError('只读解析子进程失败')
        return json.loads(done.stdout)
    except subprocess.TimeoutExpired: fallback['warnings'].append('只读解析超时，已停止解析子进程，原文件保持不变')
    except (OSError,ValueError) as e: fallback['warnings'].append(str(e)[:160])
    return fallback

if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='--worker': raise SystemExit(2)
    print(json.dumps(_extract_local(sys.argv[2],int(sys.argv[3])),ensure_ascii=False))
