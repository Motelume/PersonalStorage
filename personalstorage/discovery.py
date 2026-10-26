from pathlib import Path
import fnmatch,time
from .common import linked,relpath,safe_path,SafetyError

TEMP_PATTERNS=['~$*','*.crdownload','*.part','*.tmp']
def eligible_name(name): return not any(fnmatch.fnmatch(name.casefold(),p.casefold()) for p in TEMP_PATTERNS)
def discover(root,include_root=False,include_review=False):
    root=Path(root); inbox=root/'00_收件箱'; result=[]
    if inbox.is_dir():
        for p in sorted(inbox.iterdir()):
            if not include_review and p.name=='待确认': continue
            if eligible_name(p.name): result.append(p.relative_to(root).as_posix())
    if include_review and (inbox/'待确认').is_dir():
        result=[p for p in result if p!='00_收件箱/待确认']
        result.extend(p.relative_to(root).as_posix() for p in sorted((inbox/'待确认').iterdir()) if eligible_name(p.name))
    if include_root:
        result.extend(p.name for p in sorted(root.iterdir()) if p.is_file() and p.name.casefold()!='agents.md' and eligible_name(p.name))
    return list(dict.fromkeys(result))

def stability(path,seconds=1):
    def stat_tree():
        if linked(path): raise SafetyError('拒绝链接')
        entries=list(path.rglob('*')) if path.is_dir() else [path]
        out=[]
        for p in entries:
            if linked(p): raise SafetyError('资料包包含链接')
            if p.is_file():
                if not eligible_name(p.name): raise SafetyError('资料包含未完成/锁定文件')
                s=p.stat(); out.append((str(p.relative_to(path)) if path.is_dir() else p.name,s.st_size,s.st_mtime_ns))
        return sorted(out)
    first=stat_tree()
    if seconds: time.sleep(seconds)
    if first!=stat_tree(): raise SafetyError('文件仍在变化')
    return first

def baseline(root,state):
    root=Path(root); count=0; errors=[]
    for parent,dirs,files in __import__('os').walk(root,followlinks=False,onerror=lambda e:errors.append({'path':str(e.filename),'error':str(e)})):
        dirs[:]=[n for n in dirs if n!='_整理规则' and not linked(Path(parent)/n)]
        for n in files:
            p=Path(parent)/n
            if p==root/'AGENTS.md' or linked(p): continue
            try:
                s=p.stat(); rel=p.relative_to(root).as_posix()
                existing=state.item(rel)
                # Never overwrite an existing verified manifest with a metadata-only baseline.
                if not existing: state.observe(rel,'file',{'size':s.st_size,'mtime_ns':s.st_mtime_ns,'integrity':'metadata_only','audit':'historical_unreviewed'})
                count+=1
            except OSError as e: errors.append({'path':str(p),'error':str(e)})
    return {'files':count,'errors':errors,'content_hashed':False,'history_queued_for_review':False}
