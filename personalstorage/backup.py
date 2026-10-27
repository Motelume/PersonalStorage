"""Previewed management restore and verified, non-destructive original backups."""
import json
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path
from .common import SafetyError,instance_lock,safe_path,digest,sha,uid,now,write_json,read_json,linked
from .paths import canonical_root,program_home
from .policy import Policy
from .tasks import checkpoint


def destination(app,value):
    p=canonical_root(value)
    if not Path(value).is_absolute():raise SafetyError('请选择绝对目标目录')
    for parent in (p,*p.parents):
        if linked(parent):raise SafetyError('备份或恢复目标不能经过链接')
    for protected in (app.root,app.state.profile,program_home()):
        if p==protected or p.is_relative_to(protected) or protected.is_relative_to(p):raise SafetyError('目标必须与资料库、程序及管理状态目录分开')
    return p


def create_management(app):
    from .snapshot import management_snapshot
    folder=app.state.profile/'backups'/uid();folder.mkdir(parents=True)
    with instance_lock(app.state.profile):
        result=management_snapshot(app.root,app.state,app.policy,folder/'state.sqlite3')
    archive=folder/'management.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for p in folder.iterdir():
            if p.name!='management.zip':z.write(p,p.name)
    return {'id':folder.name,'at':now(),'path':str(archive),'kind':'management','bytes':archive.stat().st_size}


def catalog(app):
    folder=app.state.profile/'backups';rows=[]
    if folder.is_dir():
        for p in folder.glob('*/management.zip'):
            if not linked(p):rows.append({'id':p.parent.name,'path':str(p),'kind':'management','bytes':p.stat().st_size,'modified':p.stat().st_mtime})
    return {'management':sorted(rows,key=lambda r:r['modified'],reverse=True)[:50],'originals':app.state.get('original_backups',[])}


def restore_preview(app,value):
    source=Path(value)
    if linked(source) or source.suffix.lower()!='.zip' or not source.is_file():raise SafetyError('请选择管理快照ZIP文件')
    folder=app.state.profile/'restore-staging'/uid();folder.mkdir(parents=True)
    with zipfile.ZipFile(source) as archive:
        if set(archive.namelist())!={'state.sqlite3','state.sqlite3.metadata.json'}:raise SafetyError('不是受支持的管理快照包')
        if any(i.file_size>512*1024*1024 for i in archive.infolist()):raise SafetyError('管理快照超过恢复预算')
        for name in archive.namelist():
            with archive.open(name) as src,(folder/name).open('xb') as dst:shutil.copyfileobj(src,dst)
    db=sqlite3.connect((folder/'state.sqlite3').as_uri()+'?mode=ro',uri=True)
    try:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SafetyError('快照数据库校验失败')
        identity=json.loads(db.execute("SELECT value FROM settings WHERE key='identity'").fetchone()[0])
        if identity!=app.state.get('identity'):raise SafetyError('快照不属于当前资料库或卷身份不一致')
        counts={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('items','operations','reviews')}
    finally:db.close()
    meta=read_json(folder/'state.sqlite3.metadata.json');raw=meta.get('documents',{}).get('_整理规则/分类配置.json')
    config=json.loads(raw) if raw else None
    if config:
        candidate=Policy.from_data(app.root,config)
        if candidate.hash!=meta.get('policy_hash'):raise SafetyError('快照规则指纹不一致')
    return {'stage':str(folder),'database_hash':sha(folder/'state.sqlite3'),'config':config,'expected_policy':app.policy.hash,
            'counts':counts,'at':meta.get('at'),'contains_originals':False,'original_files_touched':False}


def restore(app,preview,with_rules=False):
    folder=Path(preview['stage']);dbfile=folder/'state.sqlite3'
    if not folder.resolve().is_relative_to((app.state.profile/'restore-staging').resolve()):raise SafetyError('恢复暂存位置无效')
    with instance_lock(app.state.profile):
        if Policy(app.root).hash!=preview['expected_policy'] or sha(dbfile)!=preview['database_hash']:raise SafetyError('恢复预览已过期')
        if app.state.db.execute("SELECT COUNT(*) FROM operations WHERE status IN ('prepared','moved','verified','recovery_required')").fetchone()[0]:raise SafetyError('先处理未完成文件操作再恢复管理状态')
        backup_dir=app.state.profile/'restore-backups'/uid();backup_dir.mkdir(parents=True)
        app.state.snapshot(backup_dir/'state.sqlite3');write_json(backup_dir/'classification-config.json',app.policy.data)
        # Recovery ticket is outside the database being restored.
        journal=app.state.profile/'restore-journal.json'
        write_json(journal,{'backup':str(backup_dir),'stage':str(folder),'phase':'prepared'})
        source=sqlite3.connect(dbfile)
        try:source.backup(app.state.db)
        finally:source.close()
        try:
            if with_rules:
                if not preview.get('config'):raise SafetyError('该快照没有配套规则')
                write_json(safe_path(app.root,'_整理规则/分类配置.json'),preview['config'])
            app.state.set('automation',None);app.state.set('rule_publish_journal',None)
            app.state.set('restore_notice','已恢复管理状态。请检查并恢复文件记录；实际资料没有移动。')
            app.policy=Policy(app.root);journal.unlink()
        except BaseException:
            source=sqlite3.connect(backup_dir/'state.sqlite3')
            try:source.backup(app.state.db)
            finally:source.close()
            write_json(safe_path(app.root,'_整理规则/分类配置.json'),read_json(backup_dir/'classification-config.json'))
            if journal.exists():journal.unlink()
            raise
        return {'restored':True,'rules_restored':with_rules,'backup':str(backup_dir),'original_files_touched':False,'next_step':'检查并恢复记录，再核对实际资料'}


def recover_restore(root,state):
    journal=state.profile/'restore-journal.json'
    if not journal.is_file():return
    value=read_json(journal);backup_dir=Path(value['backup'])
    if not backup_dir.resolve().is_relative_to((state.profile/'restore-backups').resolve()):raise SafetyError('恢复日志中的备份位置无效')
    db=sqlite3.connect(backup_dir/'state.sqlite3')
    try:db.backup(state.db)
    finally:db.close()
    write_json(safe_path(root,'_整理规则/分类配置.json'),read_json(backup_dir/'classification-config.json'))
    state.set('automation',None);state.set('restore_notice','上次恢复中断，已回到恢复前的管理状态；原资料未改动。');journal.unlink()


def original_preview(app,value):
    target=destination(app,value);files=[];total=0
    for parent,dirs,names in os.walk(app.root,followlinks=False):
        checkpoint();dirs[:]=[d for d in dirs if not linked(Path(parent)/d)]
        for name in names:
            checkpoint()
            path=Path(parent)/name
            if linked(path) or not path.is_file():continue
            st=path.stat();files.append({'path':path.relative_to(app.root).as_posix(),'size':st.st_size,'mtime_ns':st.st_mtime_ns});total+=st.st_size
    ancestor=target
    while not ancestor.exists():ancestor=ancestor.parent
    free=shutil.disk_usage(ancestor).free
    if free<total+100*1024*1024:raise SafetyError('目标剩余空间不足，未开始备份')
    return {'target':str(target/('PersonalStorage-backup-'+uid())),'files':files,'bytes':total,'free_bytes':free,'root':str(app.root)}


def backup_originals(app,preview,job=None):
    target=destination(app,preview['target'])
    if preview['root']!=str(app.root) or target.exists():raise SafetyError('备份目标已存在或资料库变化，请重新预览')
    target.mkdir(parents=True);records=[];progress_file=target/'backup-manifest.json'
    write_json(progress_file,{'version':1,'status':'partial','root':str(app.root),'files':[]})
    for i,item in enumerate(preview['files']):
        checkpoint();source=safe_path(app.root,item['path']);st=source.stat()
        if (st.st_size,st.st_mtime_ns)!=(item['size'],item['mtime_ns']):raise SafetyError('备份预览后源文件变化，请重新开始')
        out=safe_path(target/'files',item['path']);out.parent.mkdir(parents=True,exist_ok=True)
        with source.open('rb') as src,out.open('xb') as dst:shutil.copyfileobj(src,dst);dst.flush();os.fsync(dst.fileno())
        fingerprint=sha(out)
        if fingerprint!=sha(source) or (source.stat().st_size,source.stat().st_mtime_ns)!=(item['size'],item['mtime_ns']):raise SafetyError('源文件变化，备份标记为未完成')
        records.append({**item,'sha256':fingerprint})
        if job is not None:job.update(phase=f'原资料校验备份 {i+1}/{len(preview["files"])}')
        if i%50==0:write_json(progress_file,{'version':1,'status':'partial','root':str(app.root),'files':records})
    write_json(progress_file,{'version':1,'status':'completed','root':str(app.root),'at':now(),'files':records})
    record={'id':uid(),'path':str(target),'files':len(records),'bytes':preview['bytes'],'at':now()}
    app.state.set('original_backups',(app.state.get('original_backups',[])+[record])[-30:]);return record


def original_restore_preview(app,backup_id,target_value):
    record=next((r for r in app.state.get('original_backups',[]) if r['id']==backup_id),None)
    if not record:raise SafetyError('备份记录不可用')
    folder=Path(record['path']);data=read_json(folder/'backup-manifest.json');target=destination(app,target_value)
    if target.exists():raise SafetyError('原资料恢复必须使用一个尚不存在的新目录')
    if data.get('status')!='completed':raise SafetyError('不能将未完成备份作为完整恢复来源')
    for item in data['files']:safe_path(folder/'files',item['path']);safe_path(target,item['path'])
    return {'source':str(folder),'target':str(target),'manifest_hash':digest(data),'files':len(data['files'])}


def restore_originals(app,preview,job=None):
    source=Path(preview['source']);data=read_json(source/'backup-manifest.json');target=destination(app,preview['target'])
    if digest(data)!=preview['manifest_hash'] or target.exists():raise SafetyError('恢复来源或目标已变化')
    target.mkdir(parents=True)
    for i,item in enumerate(data['files']):
        checkpoint();src=safe_path(source/'files',item['path']);dst=safe_path(target,item['path'])
        if sha(src)!=item['sha256']:raise SafetyError('备份文件校验不通过，恢复停止')
        dst.parent.mkdir(parents=True,exist_ok=True)
        with src.open('rb') as a,dst.open('xb') as b:shutil.copyfileobj(a,b)
        if sha(dst)!=item['sha256']:raise SafetyError('恢复副本校验失败')
        if job is not None:job.update(phase=f'恢复到新目录 {i+1}/{len(data["files"])}')
    return {'restored':True,'target':str(target),'files':len(data['files']),'original_files_touched':False}
