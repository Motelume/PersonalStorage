"""On-demand local ASR/formula/handwriting/table tools; never upload originals."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import sqlite3
from contextlib import closing
from pathlib import Path
from .common import SafetyError,sha,write_json,read_json,linked
from .paths import runtime_home
from .tasks import checkpoint
from .processes import run as run_process

PACKAGES={'audio':'faster-whisper==1.2.1','formula':'pix2tex==0.1.4'}
WORKER=r'''
import sys,json,os
from pathlib import Path
job=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'));home=Path(job['home']);kind=job['kind']
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
import socket
def offline(*a,**k):raise RuntimeError('recognition worker is offline')
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=socket.getaddrinfo=offline
if kind=='audio':
 from faster_whisper import WhisperModel
 model=WhisperModel(str(home/'whisper-small'),device='cpu',compute_type='int8',local_files_only=True)
 segments,info=model.transcribe(job['path'],beam_size=3,vad_filter=True)
 rows=[];text=[]
 for segment in segments:
  if segment.end>1800:break
  rows.append({'start':round(segment.start,2),'end':round(segment.end,2),'text':segment.text});text.append(segment.text)
  if sum(map(len,text))>16000:break
 result={'text':'\n'.join(text)[:16000],'segments':rows,'language':info.language,'duration':info.duration,'complete':info.duration<=1800}
else:
 from pix2tex.cli import LatexOCR
 from PIL import Image
 from argparse import Namespace
 import pix2tex
 package=Path(pix2tex.__file__).parent
 checkpoint=package/'model/checkpoints/weights.pth'
 if not checkpoint.is_file():raise RuntimeError('formula weights unavailable; prepare component first')
 model=LatexOCR()
 with Image.open(job['path']) as img:
  if img.width*img.height>24000000:raise ValueError('image exceeds pixel budget')
  text=model(img.convert('RGB'))
 result={'text':text,'latex':text,'complete':False}
Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
'''


def home():
    folder=runtime_home()/'recognition';machine=runtime_home()/'components.sqlite3'
    if machine.is_file():
        with closing(sqlite3.connect(machine)) as db:
            row=db.execute('SELECT path FROM location WHERE id=1').fetchone()
        if row:folder=Path(row[0])
    folder.mkdir(parents=True,exist_ok=True);return folder

def configure_location(path):
    folder=Path(path).absolute()
    if not Path(path).is_absolute() or linked(folder):raise SafetyError('组件位置需为本机绝对目录')
    folder.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(runtime_home()/'components.sqlite3')) as db:
        db.execute('CREATE TABLE IF NOT EXISTS location(id INTEGER PRIMARY KEY,path TEXT)');db.execute('INSERT OR REPLACE INTO location VALUES(1,?)',(str(folder),));db.commit()
    return status()

def cached(path):
    import sqlite3
    database=home()/'results.sqlite3'
    if not database.exists():return None
    with closing(sqlite3.connect(database)) as db:
        columns={r[1] for r in db.execute('PRAGMA table_info(results)')}
        if 'size' in columns and not db.execute('SELECT 1 FROM results WHERE size=? AND extension=? LIMIT 1',(path.stat().st_size,path.suffix.lower())).fetchone():return None
        key=sha(path)
        row=db.execute('SELECT result FROM results WHERE hash=?',(key,)).fetchone()
    if not row:return None
    result=json.loads(row[0]);return {'name':path.name,'kind':'file','text':result.get('text','')[:16000],'text_source':result.get('source','local_enhanced'),'complete':result.get('complete',False),'warnings':result.get('warnings',[]),'recognition_accepted':True}

def accept(path,result):
    import sqlite3
    with closing(sqlite3.connect(home()/'results.sqlite3')) as db:
        db.execute('CREATE TABLE IF NOT EXISTS results(hash TEXT PRIMARY KEY,result TEXT,size INTEGER,extension TEXT)')
        columns={r[1] for r in db.execute('PRAGMA table_info(results)')}
        if 'size' not in columns:db.execute('ALTER TABLE results ADD COLUMN size INTEGER');db.execute('ALTER TABLE results ADD COLUMN extension TEXT')
        db.execute('INSERT OR REPLACE INTO results(hash,result,size,extension) VALUES(?,?,?,?)',(sha(path),json.dumps(result,ensure_ascii=False),path.stat().st_size,path.suffix.lower()))
        db.execute('DELETE FROM results WHERE rowid NOT IN (SELECT rowid FROM results ORDER BY rowid DESC LIMIT 2000)')
        db.commit()
    return {'accepted_for_extraction':True,'original_files_touched':False}


def python_path():
    if not getattr(sys,'frozen',False):return Path(sys.executable)
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for version in ('3.13','3.12','3.11'):
            try:
                with winreg.OpenKey(hive,'Software\\Python\\PythonCore\\'+version+'\\InstallPath') as key:
                    path=Path(winreg.QueryValueEx(key,'ExecutablePath')[0])
                if path.is_file():return path
            except OSError:pass
    raise SafetyError('音频/公式组件需本机Python 3.11–3.13；请安装官方Python后重试，核心功能不受影响')


def status():
    folder=home();marker=read_json(folder/'installed.json') if (folder/'installed.json').is_file() else {}
    return {'audio':bool(marker.get('audio') and (folder/'whisper-small/model.bin').is_file()),'formula':bool(marker.get('formula')),
            'handwriting':all((folder/n).is_file() for n in ('v5-det.onnx','v5-rec.onnx')),'tables':True,
            'location':str(folder),'automatic_audio':bool(marker.get('automatic_audio')),'no_network_during_recognition':True}


def download(url,target,expected=None,max_bytes=200*1024*1024):
    if target.exists():
        if expected and sha(target).lower()==expected.lower():return
        raise SafetyError('下载目标已有文件，请核对后换位置或清理不完整组件')
    temp=target.with_suffix(target.suffix+'.partial');count=0
    try:
        with urllib.request.urlopen(url,timeout=60) as response,temp.open('wb') as output:
            if not response.url.startswith('https://'):raise SafetyError('模型下载必须使用HTTPS')
            while block:=response.read(1024*1024):
                checkpoint();count+=len(block)
                if count>max_bytes:raise SafetyError('模型文件超过下载预算')
                output.write(block)
        if expected and sha(temp).lower()!=expected.lower():raise SafetyError('模型SHA-256校验失败')
        # Some redirected Windows user folders reject a rename across their backing stores.
        # Publish with an exclusive copy and verify the final bytes; never replace an existing file.
        with temp.open('rb') as source,target.open('xb') as output:
            shutil.copyfileobj(source,output);output.flush();os.fsync(output.fileno())
        if sha(target)!=sha(temp):raise SafetyError('下载副本校验失败')
    finally:
        if temp.exists():temp.unlink()


def install(kind,job=None):
    folder=home();marker=read_json(folder/'installed.json') if (folder/'installed.json').is_file() else {}
    if kind=='handwriting':
        import rapidocr,yaml
        catalog=yaml.safe_load((Path(rapidocr.__file__).parent/'default_models.yaml').read_text(encoding='utf-8'))['onnxruntime']['PP-OCRv5']
        for key,model,out in [('det','ch_PP-OCRv5_mobile_det.onnx','v5-det.onnx'),('rec','ch_PP-OCRv5_rec_server_infer.onnx','v5-rec.onnx')]:
            checkpoint();entry=catalog[key][model]
            if job is not None:job['phase']='按需下载并校验 PP-OCRv5 '+key
            download(entry['model_dir'],folder/out,entry['SHA256'])
        marker[kind]=True
    elif kind in PACKAGES:
        executable=folder/'env/Scripts/python.exe'
        if not executable.is_file():run_process([str(python_path()),'-m','venv',str(folder/'env')],timeout=120)
        if job is not None:job['phase']='安装可选本地组件 '+kind+'；首次需下载依赖'
        run_process([str(executable),'-m','pip','install','--disable-pip-version-check',PACKAGES[kind]]+(['av==15.1.0'] if kind=='audio' else []),timeout=1800,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if kind=='audio':
            code="from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-small',local_dir="+repr(str(folder/'whisper-small'))+")"
        else:code='from pix2tex.cli import LatexOCR; LatexOCR()'
        run_process([str(executable),'-c',code],timeout=1800,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        marker[kind]=True
    else:raise SafetyError('未知组件')
    write_json(folder/'installed.json',marker);return status()


def infer(kind,path):
    folder=home();capability=status()
    if kind not in ('audio','formula','handwriting','table'):raise SafetyError('识别模式无效')
    if kind in ('audio','formula'):
        if not capability[kind]:raise SafetyError('请先明确准备对应本地组件')
        worker=folder/'recognition-worker.py';worker.write_text(WORKER,encoding='utf-8')
        with tempfile.TemporaryDirectory(prefix='personalstorage-recognition-') as temp:
            task=Path(temp)/'task.json';result=Path(temp)/'result.json';write_json(task,{'home':str(folder),'kind':kind,'path':str(path)})
            env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1'}
            run_process([str(folder/'env/Scripts/python.exe'),str(worker),str(task),str(result)],timeout=600 if kind=='audio' else 120,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            return {**read_json(result),'source':'local_'+kind,'warnings':['识别结果需核对；不代表分类已经确认']}
    return image_structure(path,kind)


def image_structure(path,kind):
    from . import ocr
    from PIL import Image,ImageOps
    import numpy as np
    with Image.open(path) as original:
        if original.width*original.height>24000000:raise SafetyError('图像超过2400万像素预算')
        image=ImageOps.exif_transpose(original).convert('RGB');image.thumbnail((2400,2400));array=np.asarray(image)
    if kind=='handwriting':
        if not status()['handwriting']:raise SafetyError('先准备手写增强模型')
        from rapidocr import RapidOCR,OCRVersion
        params={'Global.log_level':'critical','Det.model_path':str(home()/'v5-det.onnx'),'Rec.model_path':str(home()/'v5-rec.onnx'),'Det.ocr_version':OCRVersion.PPOCRV5,'Rec.ocr_version':OCRVersion.PPOCRV5,'EngineConfig.onnxruntime.intra_op_num_threads':2}
        params['Global.font_path']=str(next((p for p in (Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts').glob('segoeui.ttf')),Path('')));params['Cls.model_path']=str(ocr.model_folder()/ocr.MODELS['Cls'][0])
        with ocr.offline():output=RapidOCR(params=params)(array,text_score=.4)
    else:
        with ocr.offline():output=ocr.engine()(array,text_score=.5)
    boxes=output.boxes if hasattr(output,'boxes') else output.dt_polys
    lines=[{'text':str(t),'confidence':float(s),'box':[[float(x),float(y)] for x,y in b]} for t,s,b in zip([] if output.txts is None else list(output.txts),[] if output.scores is None else list(output.scores),boxes if boxes is not None else [])]
    lines.sort(key=lambda r:(sum(p[1] for p in r['box'])/4,sum(p[0] for p in r['box'])/4))
    rows=[]
    for line in lines:
        center=sum(p[1] for p in line['box'])/4;height=max(p[1] for p in line['box'])-min(p[1] for p in line['box'])
        if not rows or abs(rows[-1]['center']-center)>max(8,height*.6):rows.append({'center':center,'cells':[]})
        rows[-1]['cells'].append(line)
    grid=[[c['text'] for c in sorted(r['cells'],key=lambda c:min(p[0] for p in c['box']))] for r in rows]
    text='\n'.join('\t'.join(row) for row in grid)
    return {'text':text[:16000],'lines':lines[:400],'rows':grid[:200],'source':'local_'+kind,'complete':False,
            'warnings':['手写增强仍可能误识别，需人工核对' if kind=='handwriting' else '按文字坐标重建行列；复杂合并单元格和跨页结构需核对']}
