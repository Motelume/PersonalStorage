"""Explicit, resumable-by-cache whole-library location review; never moves files."""
from pathlib import Path
import json
from .common import SafetyError,digest,manifest,safe_path,linked,now,uid,instance_lock
from .discovery import eligible_name
from .extraction import extract
from .ocr import IMAGES

def content_fingerprint(m):
    return digest({'kind':m['kind'],'directories':m.get('directories',[]),'files':[{k:f[k] for k in ('path','size','sha256')} for f in m['files']]})

def cached(app,path,m,model,note=None):
    if note is None:note=app.state.get('classification_notes',{}).get(path,'')
    row=app.state.db.execute('SELECT * FROM ai_location_cache WHERE path=?',(path,)).fetchone()
    if row and row['fingerprint']==content_fingerprint(m) and row['policy_hash']==app.policy.hash and row['model']==model:
        data=json.loads(row['data'])
        item=app.state.item(path);a=app.state.db.execute('SELECT data FROM assignments WHERE item_id=?',(item['id'],)).fetchone() if item else None
        if data.get('note_hash')==digest(note) and data.get('assignment_hash')==digest(json.loads(a[0]) if a else {}) and data.get('protocol')=='location-1':return data

def save(app,path,m,model,decision,reason,target=None,note=''):
    item=app.state.item(path);a=app.state.db.execute('SELECT data FROM assignments WHERE item_id=?',(item['id'],)).fetchone() if item else None
    data={'decision':decision,'reason':reason,'target_parent':target,'note_hash':digest(note),'assignment_hash':digest(json.loads(a[0]) if a else {}),'protocol':'location-1'}
    with app.state.db:app.state.db.execute('INSERT OR REPLACE INTO ai_location_cache VALUES(?,?,?,?,?,?)',
        (path,content_fingerprint(m),app.policy.hash,model,json.dumps(data,ensure_ascii=False),now()))

def features(path,limit=4000):
    result=extract(path,limit=limit)
    if Path(path).is_dir():
        result['warnings']=[w for w in result.get('warnings',[]) if '未递归解析正文' not in w]
        text=[];budget=limit
        # Representative documents only; packages remain intact. No scripts run.
        candidates=[]
        for parent,dirs,files in __import__('os').walk(path,followlinks=False):
            dirs[:]=[n for n in dirs if n not in ('.git','node_modules','.venv','venv','__pycache__') and not linked(Path(parent)/n)]
            if len(Path(parent).relative_to(path).parts)>2:dirs[:]=[]
            for name in files:
                p=Path(parent)/name
                if p.suffix.lower() in IMAGES|{'.md','.txt','.pdf','.docx','.pptx'} and not linked(p):candidates.append(p)
            if len(candidates)>=30:break
        candidates.sort(key=lambda p:(p.name.casefold() not in ('readme.md','readme.txt'),p.suffix.lower() in IMAGES,len(p.parts),p.name))
        for p in candidates[:3]:
            item=extract(p,limit=max(1,budget))
            fragment=item.get('text','')[:budget]
            if fragment:text.append(p.relative_to(path).as_posix()+':\n'+fragment);budget-=len(fragment)
            if budget<=0:break
        result['text']='\n\n'.join(text)[:limit]
        result.setdefault('warnings',[]).append('完整资料包按整体判断，仅阅读有限代表文档；证据不足须待确认')
    return result

def units(app):
    """Independent files plus whole projects/contests/technical packages."""
    out=[]
    sentinels={'.git','package.json','pyproject.toml','Cargo.toml','CMakeLists.txt'}
    def visit(folder,force_bundle=False):
        if linked(folder):return
        if folder.is_file():
            if eligible_name(folder.name) and folder.name.casefold() not in ('agents.md','desktop.ini','thumbs.db'):out.append(folder.relative_to(app.root).as_posix())
            return
        children=list(folder.iterdir())
        names={p.name for p in children}
        if force_bundle or names.intersection(sentinels) or any(p.suffix.lower()=='.tex' for p in children if p.is_file()):
            out.append(folder.relative_to(app.root).as_posix());return
        for p in sorted(children):visit(p)
    for category in sorted(app.policy.categories-{'09_移动硬盘管理'}):
        folder=safe_path(app.root,category)
        if not folder.is_dir():continue
        for p in sorted(folder.iterdir()):visit(p,category in ('02_项目','03_竞赛') and p.is_dir())
    staging=safe_path(app.root,'09_移动硬盘管理/待移入硬盘')
    if staging.is_dir():
        for p in sorted(staging.iterdir()):visit(p,p.is_dir())
    for p in sorted(app.root.iterdir()):
        if p.is_file() and p.suffix.lower()!='.lnk':visit(p)
    return out

def run(app,manager,job,stop,max_batches=20):
    with instance_lock(app.state.profile/'library-audit-lease'):
        return _run(app,manager,job,stop,max_batches)

def _run(app,manager,job,stop,max_batches=20):
    config=manager.settings()
    if not config['enabled'] or not manager.vault.configured():raise SafetyError('请先启用AI并保存密钥')
    if not isinstance(max_batches,int) or isinstance(max_batches,bool) or not 1<=max_batches<=1000:raise SafetyError('本次批数须为1至1000')
    paths=units(app);report={'id':uid(),'started_at':now(),'status':'running','total':len(paths),'hashed':0,'cache_hits':0,'ai_items':0,'batches':0,
        'approved':0,'suggestions':0,'needs_input':0,'errors':0,'empty_units':0,'remaining':len(paths),'tokens':0,'files_moved':False}
    app.state.set('library_audit',report)
    pending=[]
    def flush():
        if not pending:return True
        if stop.is_set() or report['batches']>=max_batches:return False
        if manager.status()['calls_today']>=config['daily_limit']:report['status']='daily_limit';return False
        packet=manager.prepare([{'path':p,'audit_mode':True,'action':{'decision':'review'}} for p in pending])
        result=manager.run(packet);report['batches']+=1;report['ai_items']+=len(pending);report['tokens']+=result['usage'].get('total_tokens',0)
        for row in result['results']:
            status=row['status'];report['approved' if status=='confirmed' else 'suggestions' if status=='correction_required' else 'needs_input']+=1
        pending.clear();app.state.set('library_audit',report);return True
    for index,path in enumerate(paths):
        if stop.is_set():report['status']='stopped';break
        job.update(phase='全库位置复核：本地校验与增量审核',completed=index,total=len(paths))
        try:
            m=manifest(safe_path(app.root,path));report['hashed']+=1
            if not m['files']:report['empty_units']+=1;continue
            old=cached(app,path,m,config['model'])
            if old and old['decision']=='confirm':report['cache_hits']+=1;report['approved']+=1
            elif old and old['decision']=='needs_user_input':report['cache_hits']+=1;report['needs_input']+=1
            else:pending.append(path)
            if len(pending)>=config['max_items'] and not flush():report['status']=report['status'] if report['status']=='daily_limit' else 'stopped' if stop.is_set() else 'batch_limit';break
        except (OSError,SafetyError) as exc:
            report['errors']+=1;report['last_error']=str(exc)[:500];pending.clear()
            if '余额' in str(exc) or '密钥' in str(exc) or '超时' in str(exc):report['status']='error';break
        report['remaining']=len(paths)-index-1
        app.state.set('library_audit',report)
    else:
        try:
            if pending and not flush():report['status']='stopped' if stop.is_set() else 'batch_limit'
            elif report['status']=='running':report['status']='completed'
        except (OSError,SafetyError) as exc:report.update(status='error',last_error=str(exc)[:500]);report['errors']+=1
    if report['status']=='running':report['status']='stopped' if stop.is_set() else 'batch_limit'
    report['remaining']=max(0,report['total']-report['approved']-report['suggestions']-report['needs_input']-report['empty_units'])
    if report['status']=='completed' and report['remaining']:report['status']='partial'
    report['finished_at']=now();app.state.set('library_audit',report)
    return report
