"""Explicit machine-local inbox roots. External paths never enter the core resolver."""
from pathlib import Path
import os
import re
from .paths import canonical_root,program_home,runtime_home
from .common import SafetyError,linked,relpath,safe_path,uid,now,digest
from .discovery import eligible_name
from .scanning import metadata
from .extraction import extract

PREFIX='@inbox/'

def settings(state):return state.get('inboxes',{'internal_enabled':True,'sources':[]})

def validate_folder(root,value):
    if not isinstance(value,str) or not value.strip() or not Path(value).expanduser().is_absolute():raise SafetyError('请输入绝对文件夹路径')
    folder=canonical_root(value)
    if str(folder).startswith('\\\\'):raise SafetyError('本版收件箱仅支持本机磁盘目录')
    if not folder.is_dir() or folder==Path(folder.anchor):raise SafetyError('请选择存在的具体文件夹，不能选择整个磁盘')
    for parent in (folder,*folder.parents):
        if linked(parent):raise SafetyError('收件箱路径不能经过链接、junction或重解析点')
    # Resolve Windows short-name aliases only after rejecting linked ancestors.
    # An alias must not let the library or program register itself as an inbox.
    folder=folder.resolve()
    protected=[canonical_root(root),program_home(),runtime_home(),canonical_root(os.environ.get('WINDIR',r'C:\Windows'))]
    protected.extend(canonical_root(os.environ[name]) for name in ('ProgramFiles','ProgramFiles(x86)','PROGRAMDATA') if os.environ.get(name))
    for other in protected:
        other=other.resolve()
        if folder==other or folder.is_relative_to(other) or other.is_relative_to(folder):raise SafetyError('外部收件箱不能包含资料库、程序、运行状态或系统目录，也不能位于其中')
    return folder

def source_record(state,ident,enabled=True):
    found=next((s for s in settings(state)['sources'] if s['id']==ident),None)
    if not found or (enabled and not found['enabled']):raise SafetyError('外部收件箱不存在或已停用')
    return found

def source_root(root,state,ident,enabled=True):
    record=source_record(state,ident,enabled);folder=validate_folder(root,record['path']);st=folder.stat()
    if [st.st_dev,st.st_ino]!=record['identity']:raise SafetyError('来源目录身份已变化，请在收件箱管理中重新添加')
    return folder

def resolve(root,state,logical,*,enabled=True):
    if not logical.startswith(PREFIX):return safe_path(root,logical)
    parts=relpath(logical).split('/')
    if len(parts)<3 or not re.fullmatch('[0-9a-f]{32}',parts[1]):raise SafetyError('外部来源标识无效')
    return safe_path(source_root(root,state,parts[1],enabled),'/'.join(parts[2:]))

def add(root,state,path,label=''):
    folder=validate_folder(root,path);data=settings(state)
    if sum(not s.get('removed') for s in data['sources'])>=20:raise SafetyError('最多登记20个外部收件箱')
    for old in data['sources']:
        previous=canonical_root(old['path'])
        if folder==previous and old['enabled']:raise SafetyError('这个文件夹已登记为收件箱')
        if old['enabled'] and (folder.is_relative_to(previous) or previous.is_relative_to(folder)):raise SafetyError('收件箱之间不能嵌套，避免重复扫描')
    st=folder.stat();record={'id':uid(),'path':str(folder),'label':str(label).strip()[:80] or folder.name,'enabled':True,'identity':[st.st_dev,st.st_ino],'added_at':now()}
    data['sources'].append(record);state.set('inboxes',data);return record

def toggle(state,ident,enabled):
    if not isinstance(enabled,bool):raise SafetyError('启用状态须为布尔值')
    data=settings(state)
    if ident=='internal':data['internal_enabled']=enabled
    else:
        record=next((s for s in data['sources'] if s['id']==ident),None)
        if not record:raise SafetyError('收件箱未登记')
        if record.get('removed'):raise SafetyError('来源已移除，请重新添加')
        if enabled:
            folder=source_root(state.root,state,ident,enabled=False)
            for other in data['sources']:
                if other['id']!=ident and other['enabled']:
                    path=canonical_root(other['path'])
                    if folder==path or folder.is_relative_to(path) or path.is_relative_to(folder):raise SafetyError('已启用来源之间不能重叠')
        record['enabled']=enabled
    state.set('inboxes',data);return {'updated':True,'files_touched':False}

def remove(state,ident):
    data=settings(state);record=next((s for s in data['sources'] if s['id']==ident),None)
    if not record:raise SafetyError('只能移除已登记的外部来源')
    record.update(enabled=False,removed=True)
    with state.db:
        for row in state.db.execute("SELECT r.id,r.data FROM reviews r JOIN items i ON i.id=r.item_id WHERE i.path LIKE ? AND r.status IN ('pending','waiting_user','waiting_content','correction_required')",(PREFIX+ident+'/%',)).fetchall():
            import json
            content=json.loads(row['data']);content['resolution']={'kind':'source_removed','at':now(),'reason':'用户移除来源，仅关闭该来源待办；未修改文件'}
            state.db.execute("UPDATE reviews SET status='superseded',data=? WHERE id=?",(json.dumps(content,ensure_ascii=False),row['id']))
    state.set('inboxes',data);return {'removed':True,'files_touched':False,'history_retained':True}

def listing(root,state):
    data=settings(state);out=[{'id':'internal','label':'库内收件箱','path':str(Path(root)/'00_收件箱'),'enabled':data['internal_enabled'],'internal':True,'available':True}]
    for s in data['sources']:
        if s.get('removed'):continue
        item={**s,'internal':False}
        try:source_root(root,state,s['id'],enabled=False);item['available']=True
        except (OSError,SafetyError) as exc:item.update(available=False,error=str(exc))
        out.append(item)
    downloads=Path.home()/'Downloads'
    return {'sources':out,'suggested_downloads':str(downloads) if downloads.is_dir() else None,'import_mode':'verified_copy_keep_source','maximum':20}

def scan_external(root,state,policy,cache,*,progress=None):
    rows=[];errors=[];skipped=0
    for s in settings(state)['sources']:
        if not s['enabled']:continue
        try:
            folder=source_root(root,state,s['id'])
            for p in sorted(folder.iterdir()):
                if not eligible_name(p.name) or p.name.casefold() in ('agents.md','desktop.ini','thumbs.db'):continue
                logical=PREFIX+s['id']+'/'+p.name
                try:
                    m=metadata(p)
                    if not m['file_count']:continue
                    mark=state.db.execute('SELECT data FROM inbox_imports WHERE source=?',(logical,)).fetchone()
                    if mark:
                        import json
                        old=json.loads(mark['data']);target=safe_path(root,old['target'])
                        if old['source_metadata']==m['fingerprint'] and target.exists() and metadata(target)['fingerprint']==old['target_metadata']:
                            skipped+=1;continue
                    key=(logical,m['fingerprint'],policy.hash);features=cache.get(key)
                    if features is None:features=extract(p);cache[key]=features
                    if metadata(p)!=m:raise SafetyError('来源仍在变化，请稍后重试')
                    decision=policy.classify(features)
                    row={'source':logical,'source_id':s['id'],'source_label':s['label'],'absolute_path':str(p),'kind':m['kind'],'file_count':m['file_count'],
                         'scan_fingerprint':m['fingerprint'],'integrity':'metadata_only','external':True,**decision}
                    if decision['decision']=='move':row['target']=policy.target(decision['target_parent'],p.name)
                    else:row.update(reason=decision['reason'],evidence=decision['reason'],rule_id='unresolved')
                    if row.get('target') and safe_path(root,row['target']).exists():row.update(decision='conflict',reason='库内已有同名目标，保留源文件')
                    rows.append(row)
                except (OSError,SafetyError) as exc:rows.append({'source':logical,'source_label':s['label'],'absolute_path':str(p),'external':True,'decision':'skip','reason':str(exc)})
        except (OSError,SafetyError) as exc:errors.append({'source_id':s['id'],'label':s['label'],'error':str(exc)})
        if progress:progress(len(rows))
    return {'rows':rows,'source_errors':errors,'already_imported':skipped}
