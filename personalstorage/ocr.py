"""Offline OCR, bounded page recognition and private content-addressed caching."""
from contextlib import closing,contextmanager
from functools import lru_cache
from pathlib import Path
import hashlib
import importlib.util
import json
import math
import os
import socket
import sqlite3
import sys
import time
from .common import SafetyError,now,linked
from .paths import runtime_home

PROTOCOL='local-ocr-1-rapidocr-3.4.2-ppocrv4'
IMAGES={'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff','.gif'}
DEFAULTS={'enabled':True,'max_pages':5,'min_confidence':.85,'timeout':60}
MODELS={'Det':('ch_PP-OCRv4_det_infer.onnx','d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9'),
        'Rec':('ch_PP-OCRv4_rec_infer.onnx','48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b'),
        'Cls':('ch_ppocr_mobile_v2.0_cls_infer.onnx','e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c')}

def database():
    path=runtime_home()/'ocr/cache.sqlite3';path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=15)
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.executescript('CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,data TEXT NOT NULL,used_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);')
    except BaseException:
        db.close();raise
    return db

def settings():
    path=runtime_home()/'ocr/cache.sqlite3'
    if not path.exists():return dict(DEFAULTS)
    try:
        with closing(database()) as db:row=db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
        return validate({**DEFAULTS,**json.loads(row[0])}) if row else dict(DEFAULTS)
    except (sqlite3.Error,ValueError,TypeError,SafetyError):return dict(DEFAULTS)

def validate(data):
    value={k:data.get(k,v) for k,v in DEFAULTS.items()}
    if not isinstance(value['enabled'],bool):raise SafetyError('OCR启用状态无效')
    for key,low,high in [('max_pages',1,20),('timeout',15,180)]:
        if isinstance(value[key],bool) or not isinstance(value[key],int) or not low<=value[key]<=high:raise SafetyError('OCR页数或超时范围无效')
    score=value['min_confidence']
    if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or not .5<=score<=.99:raise SafetyError('识别置信度须在0.50至0.99之间')
    return value

def configure(data):
    value=validate({**settings(),**data})
    with closing(database()) as db,db:db.execute("INSERT OR REPLACE INTO meta VALUES('settings',?)",(json.dumps(value),))
    return {'saved':True,'settings':value,'original_files_touched':False}

def model_folder():
    if getattr(sys,'frozen',False):return Path(sys._MEIPASS)/'rapidocr/models'
    spec=importlib.util.find_spec('rapidocr')
    return Path(spec.origin).parent/'models' if spec and spec.origin else None

@lru_cache(maxsize=6)
def verify_model(path,size,mtime):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def availability():
    try:
        for name in ('rapidocr','onnxruntime','PIL','pypdfium2'):
            if importlib.util.find_spec(name) is None:return {'ready':False,'reason':'缺少本地OCR组件，请安装requirements-ocr.txt或使用完整桌面包'}
        folder=model_folder()
        for name,expected in MODELS.values():
            p=folder/name
            if not p.is_file() or linked(p):return {'ready':False,'reason':'本地识别模型缺失，不会自动联网下载'}
            st=p.stat()
            if verify_model(str(p),st.st_size,st.st_mtime_ns)!=expected:return {'ready':False,'reason':'本地识别模型校验失败，请重新安装完整程序包'}
        return {'ready':True,'reason':'中文/英文离线组件与模型已就绪'}
    except (OSError,ImportError,ValueError):return {'ready':False,'reason':'本地OCR组件不可读取'}

def signature(options=None):
    value=options or settings()
    epoch=0
    if (runtime_home()/'ocr/cache.sqlite3').exists():
        try:
            with closing(database()) as db:row=db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()
            epoch=int(row[0]) if row else 0
        except (sqlite3.Error,ValueError):epoch=-1
    return hashlib.sha256(json.dumps([PROTOCOL,value,availability()['ready'],epoch],sort_keys=True).encode()).hexdigest()

def cache_get(key):
    with closing(database()) as db,db:
        row=db.execute('SELECT data FROM cache WHERE key=?',(key,)).fetchone()
        epoch=db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()
        if row:
            db.execute('UPDATE cache SET used_at=? WHERE key=?',(now(),key))
            db.execute("INSERT INTO meta VALUES('hits','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
    return (json.loads(row[0]) if row else None),int(epoch[0]) if epoch else 0

def cache_put(key,result,epoch):
    if result.get('ocr',{}).get('status') in ('error','timeout','unavailable'):return
    payload={k:v for k,v in result.items() if k!='name'}
    with closing(database()) as db,db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()
        if (int(current[0]) if current else 0)!=epoch:return
        db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',(key,json.dumps(payload,ensure_ascii=False),now()))
        db.execute("INSERT INTO meta VALUES('ocr_pages',?) ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+CAST(excluded.value AS INTEGER)",(str(result.get('ocr',{}).get('recognized_pages',0)),))
        rows=db.execute('SELECT key,length(CAST(data AS BLOB)) FROM cache ORDER BY used_at DESC,rowid DESC').fetchall();total=0
        for index,(ident,size) in enumerate(rows):
            total+=size
            if index>=2000 or total>32*1024*1024:db.execute('DELETE FROM cache WHERE key=?',(ident,))

def clear_cache():
    try:
        with closing(database()) as db:
            with db:
                db.execute('DELETE FROM cache')
                db.execute("INSERT INTO meta VALUES('epoch','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
            db.execute('VACUUM')
    except sqlite3.DatabaseError as exc:
        if getattr(exc,'sqlite_errorcode',None) not in (sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_NOTADB):raise
        path=runtime_home()/'ocr/cache.sqlite3'
        if linked(path) or path.resolve().parent!=(runtime_home()/'ocr').resolve():raise SafetyError('缓存位置异常，未重建')
        backup=path.with_name('cache-corrupt-'+str(time.time_ns())+'.sqlite3');path.rename(backup)
        with closing(database()) as db,db:db.execute("INSERT OR REPLACE INTO meta VALUES('epoch',?)",(str(time.time_ns()),))
    return {'cleared':True,'original_files_touched':False}

def status():
    result={'settings':settings(),'engine':'RapidOCR / PP-OCRv4 / ONNX CPU','offline':True,**availability(),'cached_items':0,'cache_bytes':0,'cache_hits':0,'recognized_pages':0}
    path=runtime_home()/'ocr/cache.sqlite3'
    if path.exists():
        try:
            with closing(database()) as db:
                count,size=db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(data AS BLOB))),0) FROM cache').fetchone()
                values=dict(db.execute('SELECT key,value FROM meta'))
            result.update(cached_items=count,cache_bytes=size,cache_hits=int(values.get('hits',0)),recognized_pages=int(values.get('ocr_pages',0)))
        except (sqlite3.Error,ValueError):result['cache_warning']='本地缓存不可用，可点击清空缓存重建；原资料不受影响'
    return result

def self_test():
    import tempfile
    from PIL import Image,ImageDraw,ImageFont
    from .extraction import extract
    start=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='personalstorage-ocr-selftest-') as folder:
        path=Path(folder)/'sample.png';image=Image.new('RGB',(1100,250),'white')
        font=ImageFont.truetype(str(Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts/arial.ttf'),48)
        ImageDraw.Draw(image).text((40,70),'PersonalStorage OCR 123',font=font,fill='black');image.save(path);image.close()
        result=extract(path,options={**settings(),'enabled':True,'min_confidence':.75,'max_pages':1},use_cache=False)
    if 'personalstorage' not in result.get('text','').replace(' ','').casefold():raise SafetyError('OCR自检未通过：'+'；'.join(result.get('warnings',[])))
    return {'ok':True,'offline':True,'text':result['text'],'elapsed_ms':round((time.monotonic()-start)*1000)}

@contextmanager
def offline():
    def denied(*args,**kwargs):raise SafetyError('OCR工作进程禁止联网')
    originals=(socket.socket.connect,socket.socket.connect_ex,socket.create_connection,socket.getaddrinfo)
    socket.socket.connect=socket.socket.connect_ex=socket.create_connection=socket.getaddrinfo=denied
    try:yield
    finally:socket.socket.connect,socket.socket.connect_ex,socket.create_connection,socket.getaddrinfo=originals

_ENGINE=None
def engine():
    global _ENGINE
    if _ENGINE is not None:return _ENGINE
    capability=availability()
    if not capability['ready']:raise SafetyError(capability['reason'])
    from rapidocr import RapidOCR
    fonts=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts'
    font=next((fonts/n for n in ('segoeui.ttf','arial.ttf','msyh.ttc') if (fonts/n).is_file()),None)
    if font is None:raise SafetyError('缺少本机字体组件，识别未启动')
    params={'Global.log_level':'critical','Global.font_path':str(font),'EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':1}
    for stage,(name,_) in MODELS.items():params[stage+'.model_path']=str(model_folder()/name)
    _ENGINE=RapidOCR(params=params)
    return _ENGINE

def recognize(image,options):
    # Always pass an in-memory local image, never a URL.
    output=engine()(image,text_score=.5)
    texts=[] if output.txts is None else list(output.txts)
    scores=[] if output.scores is None else [float(s) for s in output.scores]
    accepted=[str(t) for t,s in zip(texts,scores) if s>=options['min_confidence']]
    low=sum(s<options['min_confidence'] for s in scores)
    return '\n'.join(accepted),{'confidence':round(sum(scores)/len(scores),4) if scores else None,'low_confidence_lines':low,'status':'low_confidence' if low else 'recognized' if accepted else 'empty'}

def read_document(path,limit=16000,options=None):
    options=validate(options or settings());p=Path(path)
    result={'name':p.name,'kind':'file','text':'','complete':False,'warnings':[],'text_source':'none','ocr':{'status':'not_needed','cache_hit':False,'pages':[],'recognized_pages':0,'protocol':PROTOCOL}}
    meta=result['ocr'];fragments=[];total=0;truncated=False
    def append(page_number,text,source,info=None):
        nonlocal truncated
        budget=max(0,limit-sum(len(s) for s in fragments));fragment=text[:budget]
        if fragment:fragments.append(fragment)
        truncated=truncated or len(text)>budget
        meta['pages'].append({'page':page_number,'source':source,'characters':len(fragment),**(info or {})})
    def run_image(image,number,native=''):
        from PIL import ImageOps
        if not options['enabled']:
            append(number,native,'native' if native else 'none',{'status':'disabled'});return
        if not availability()['ready']:
            append(number,native,'native' if native else 'none',{'status':'unavailable'});result['warnings'].append(availability()['reason']);return
        if image.width*image.height>24_000_000:raise SafetyError('图像超过2400万像素预算，请缩小后重试')
        normalized=ImageOps.exif_transpose(image).convert('RGB')
        try:
            normalized.thumbnail((2400,2400));text,info=recognize(normalized,options)
        finally:normalized.close()
        if native and native.strip() not in text:text=native+'\n'+text
        append(number,text,'ocr',info);meta['recognized_pages']+=1
    try:
        with offline():
            if p.suffix.lower()=='.pdf':
                import pypdfium2 as pdfium
                import pypdfium2.raw as raw
                doc=pdfium.PdfDocument(str(p))
                try:
                    total=len(doc)
                    for i in range(min(total,options['max_pages'])):
                        page=doc[i]
                        try:
                            textpage=page.get_textpage()
                            try:
                                count=textpage.count_chars();text=textpage.get_text_range(0,min(count,limit+1)) if count else ''
                            finally:textpage.close()
                            image_objects=page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE],max_depth=2)
                            has_image=next(image_objects,None) is not None
                            image_objects.close()
                            needs_ocr=not text.strip() or (len(''.join(text.split()))<20 and has_image)
                            if not needs_ocr or not options['enabled'] or not availability()['ready']:
                                append(i+1,text,'native' if text.strip() else 'none',{'status':'native' if text.strip() else 'disabled' if not options['enabled'] else 'unavailable'})
                                if needs_ocr and options['enabled'] and not availability()['ready']:result['warnings'].append(availability()['reason'])
                            else:
                                w,h=page.get_size()
                                if not all(math.isfinite(n) and n>0 for n in (w,h)):raise SafetyError('PDF页面尺寸无效')
                                bitmap=page.render(scale=min(2.2,2400/max(w,h)),may_draw_forms=False)
                                try:
                                    image=bitmap.to_pil()
                                    try:run_image(image,i+1,text)
                                    finally:image.close()
                                finally:bitmap.close()
                        finally:page.close()
                        if sum(len(s) for s in fragments)>=limit:truncated= i+1<total or truncated;break
                finally:doc.close()
            else:
                from PIL import Image
                with Image.open(p) as original:
                    total=getattr(original,'n_frames',1)
                    for i in range(min(total,options['max_pages'])):
                        original.seek(i);run_image(original,i+1)
                        if sum(len(s) for s in fragments)>=limit:truncated=i+1<total or truncated;break
    except Exception as exc:
        result['warnings'].append('本地识别未完成：'+type(exc).__name__+' '+str(exc)[:160]);meta['status']='error'
    result['text']='\n'.join(fragments)[:limit];sources={p['source'] for p in meta['pages']}
    result['text_source']='mixed' if 'native' in sources and 'ocr' in sources else 'local_ocr' if 'ocr' in sources else 'native' if 'native' in sources else 'none'
    low=sum(p.get('low_confidence_lines',0) for p in meta['pages']);meta.update(total_pages=total,processed_pages=len(meta['pages']),low_confidence_lines=low)
    if meta['status']!='error':
        states={p['status'] for p in meta['pages']}
        meta['status']='unavailable' if 'unavailable' in states else 'disabled' if 'disabled' in states else 'low_confidence' if low else 'recognized' if meta['recognized_pages'] else 'native'
    result['complete']=not truncated and len(meta['pages'])==total and not low and meta['status'] not in ('error','disabled','unavailable')
    if len(meta['pages'])<total:result['warnings'].append(f'仅处理前{len(meta["pages"])}页/帧，共{total}页/帧；未读取部分不代表无文字')
    if low:result['warnings'].append(f'{low}行低于置信度门槛，未用作分类正文，请人工核对')
    if truncated:result['warnings'].append('识别文字超过本次摘录上限，结果不完整')
    return result
