from pathlib import Path, PurePosixPath
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
import os, json, hashlib, uuid, stat

class SafetyError(Exception): pass
class ConflictError(SafetyError): pass

def now(): return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
def uid(): return uuid.uuid4().hex
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def read_json(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uid()+'.tmp')
    with temp.open('x',encoding='utf-8',newline='\n') as f:
        json.dump(value,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(temp,path)  # Only generated management records, never original files.

def relpath(value):
    s=str(value).replace('\\','/')
    if not s or s.startswith('/') or ':' in s or any(x in ('','..','.') for x in s.split('/')):
        raise SafetyError('非法相对路径: '+s)
    return PurePosixPath(s).as_posix()

def linked(p):
    if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()): return True
    return bool(os.name=='nt' and p.exists() and getattr(p.stat(follow_symlinks=False),'st_file_attributes',0) & 1024)

def safe_path(root, relative):
    root=Path(root).absolute()
    if linked(root): raise SafetyError('资料库根目录不能是链接')
    parts=relpath(relative).split('/')
    for i in range(1,len(parts)+1):
        p=root.joinpath(*parts[:i])
        if linked(p): raise SafetyError('拒绝符号链接、junction或重解析点: '+str(p))
    p=root.joinpath(*parts)
    if not p.resolve().is_relative_to(root.resolve()): raise SafetyError('路径越界')
    return p

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while block:=f.read(4*1024*1024): h.update(block)
    return h.hexdigest()

def manifest(path):
    path=Path(path)
    if linked(path): raise SafetyError('资料单元不能是链接')
    files=[]; dirs=[]
    def add(p,rel):
        if linked(p): raise SafetyError('资料包含链接: '+str(p))
        s=p.stat()
        if not stat.S_ISREG(s.st_mode): raise SafetyError('不支持特殊文件')
        h=sha(p); end=p.stat()
        if (s.st_size,s.st_mtime_ns)!=(end.st_size,end.st_mtime_ns): raise SafetyError('读取时源文件变化')
        files.append({'path':rel,'size':s.st_size,'mtime_ns':s.st_mtime_ns,'sha256':h})
    if path.is_file(): add(path,path.name)
    elif path.is_dir():
        for parent,children,names in os.walk(path,followlinks=False):
            for n in children:
                p=Path(parent)/n
                if linked(p): raise SafetyError('资料包包含链接目录')
                dirs.append(p.relative_to(path).as_posix())
            for n in names:
                p=Path(parent)/n; add(p,p.relative_to(path).as_posix())
        # Enumerate twice so concurrent addition/removal is detected as well.
        current=sorted(p.relative_to(path).as_posix() for p in path.rglob('*') if p.is_file())
        if current!=sorted(f['path'] for f in files): raise SafetyError('目录成员发生变化')
    else: raise SafetyError('源不存在')
    return {'kind':'directory' if path.is_dir() else 'file','files':sorted(files,key=lambda x:x['path']),'directories':sorted(dirs)}

def unchanged(path, expected): return manifest(path)==expected

def rename_no_replace(src,dst):
    if os.name!='nt': raise SafetyError('执行器仅支持已验证不覆盖语义的Windows')
    if dst.exists(): raise ConflictError('目标已存在')
    if src.stat().st_dev!=dst.parent.stat().st_dev: raise SafetyError('仅允许同卷移动，禁止复制删除回退')
    os.rename(src,dst)

@contextmanager
def instance_lock(profile):
    if os.name!='nt': raise SafetyError('本版仅支持Windows')
    import msvcrt
    p=Path(profile)/'executor.lock'; p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a+b') as f:
        if p.stat().st_size==0: f.write(b'0'); f.flush()
        f.seek(0)
        try: msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as e: raise SafetyError('已有执行器正在运行') from e
        try: yield
        finally:
            f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
