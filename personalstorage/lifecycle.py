"""Explicit, journaled task-folder status changes; names inside remain intact."""
from pathlib import Path
import json
from .common import SafetyError,safe_path,manifest,digest,uid,now,write_json,read_json,instance_lock,rename_no_replace
from .policy import Policy


def preview(app,task_id,status):
    if status not in ('active','completed','archived'):raise SafetyError('任务状态无效')
    matches=[t for t in app.policy.data.get('tasks',[]) if t['id']==task_id]
    if len(matches)!=1:raise SafetyError('任务标识未知或不唯一')
    task=matches[0];old=task['path'];source=safe_path(app.root,old)
    if task.get('storage')!='active' or len(Path(old).parts)<2 or not source.is_dir():raise SafetyError('只能改变本机已有任务目录的状态')
    base=source.name
    for suffix in ('（已完成）','（已归档）'):
        if base.endswith(suffix):base=base[:-len(suffix)]
    name=base+{'active':'','completed':'（已完成）','archived':'（已归档）'}[status]
    target=(Path(old).parent/name).as_posix()
    if old==target and task.get('lifecycle')==status:raise SafetyError('任务已经是所选状态')
    if target!=old and safe_path(app.root,target).exists():raise SafetyError('目标目录已经存在，不覆盖或合并')
    return {'task_id':task_id,'status':status,'source':old,'target':target,'manifest':manifest(source),'policy_hash':app.policy.hash}


def rewrite_path(value,source,target):
    return target+value[len(source):] if isinstance(value,str) and (value==source or value.startswith(source+'/')) else value


def finish(root,state,journal):
    source=journal['source'];target=journal['target'];config=journal['config'];current=read_json(safe_path(root,'_整理规则/分类配置.json'))
    if digest(current) not in (journal['policy_hash'],digest(config)):raise SafetyError('任务改名中断后规则另有修改，请保留日志并手工核对')
    if source!=target:
        if safe_path(root,source).exists() or not safe_path(root,target).is_dir() or manifest(safe_path(root,target))!=journal['manifest']:raise SafetyError('任务改名恢复条件不满足，未猜测新位置')
    write_json(safe_path(root,'_整理规则/分类配置.json'),config)
    with state.db:
        for row in state.db.execute('SELECT id,path FROM items').fetchall():
            new=rewrite_path(row['path'],source,target)
            if new!=row['path']:state.db.execute('UPDATE items SET path=? WHERE id=?',(new,row['id']))
        for row in state.db.execute('SELECT item_id,data FROM assignments').fetchall():
            data=json.loads(row['data']);old=data.get('destination_parent');data['destination_parent']=rewrite_path(old,source,target)
            if data['destination_parent']!=old:state.db.execute('UPDATE assignments SET data=?,version=version+1 WHERE item_id=?',(json.dumps(data,ensure_ascii=False),row['item_id']))
        for row in state.db.execute('SELECT id,data FROM graph_links').fetchall():
            data=json.loads(row['data'])
            for key in ('source','target'):
                if isinstance(data.get(key),dict):data[key]['path']=rewrite_path(data[key].get('path'),source,target)
                elif isinstance(data.get(key),str):data[key]=rewrite_path(data[key],source,target)
            state.db.execute('UPDATE graph_links SET data=? WHERE id=?',(json.dumps(data,ensure_ascii=False),row['id']))
    prefs=state.get('overview_preferences')
    if prefs:
        for field in ('focus','selected'):
            if field in prefs:prefs[field]=rewrite_path(prefs[field],source,target)
        prefs['expanded']=[rewrite_path(p,source,target) for p in prefs.get('expanded',[])]
        prefs['positions']={rewrite_path(p,source,target):v for p,v in prefs.get('positions',{}).items()};state.set('overview_preferences',prefs)
    state.set('automation',None);state.set('task_rename_journal',None)
    return {'renamed':source!=target,'source':source,'target':target,'status':journal['status'],'contents_preserved':True,'background_authorization_revoked':True}


def apply(app,plan):
    with instance_lock(app.state.profile):
        if Policy(app.root).hash!=plan['policy_hash'] or manifest(safe_path(app.root,plan['source']))!=plan['manifest']:raise SafetyError('任务或规则已变化，重新预览')
        if app.state.db.execute("SELECT COUNT(*) FROM operations WHERE status IN ('prepared','moved','verified','recovery_required')").fetchone()[0]:raise SafetyError('请先恢复未完成的文件操作')
        source=plan['source'];target=plan['target'];config=json.loads(json.dumps(app.policy.data))
        for task in config['tasks']:
            task['path']=rewrite_path(task['path'],source,target)
            if task['id']==plan['task_id']:task['lifecycle']=plan['status']
        for rule in config['executable_rules']:rule['target_parent']=rewrite_path(rule['target_parent'],source,target)
        for pref in config.get('explicit_preferences',[]) if isinstance(config.get('explicit_preferences'),list) else []:
            if isinstance(pref,dict) and 'destination' in pref:pref['destination']=rewrite_path(pref['destination'],source,target)
        config['policy_version']='local-task-'+uid()[:12];Policy.from_data(app.root,config)
        backup=app.state.profile/'task-backups'/uid();backup.mkdir(parents=True);app.state.snapshot(backup/'state.sqlite3');write_json(backup/'config.json',app.policy.data)
        journal={**plan,'config':config,'backup':str(backup)};app.state.set('task_rename_journal',journal);app.state.set('automation',None)
        if source!=target:rename_no_replace(safe_path(app.root,source),safe_path(app.root,target))
        result=finish(app.root,app.state,journal);app.policy=Policy(app.root)
        return {**result,'backup':str(backup)}


def recover(root,state):
    journal=state.get('task_rename_journal')
    if not journal:return
    if journal['source']!=journal['target'] and safe_path(root,journal['source']).is_dir() and not safe_path(root,journal['target']).exists():
        state.set('task_rename_journal',None);return
    finish(root,state,journal)
