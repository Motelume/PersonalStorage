from pathlib import Path
import json
from .common import safe_path,manifest,digest,now,write_json,instance_lock,rename_no_replace,SafetyError,ConflictError
from .planner import in_scope

def load_plan(state,planid):
    row=state.db.execute('SELECT data FROM plans WHERE id=?',(planid,)).fetchone()
    if not row: raise SafetyError('计划ID不存在；只执行本机已登记计划')
    return json.loads(row[0])

def execute(root,state,policy,planid,*,crash_hook=None,lock=True):
    if lock:
        with instance_lock(state.profile): return execute(root,state,policy,planid,crash_hook=crash_hook,lock=False)
    if state.get('paused',False): raise SafetyError('自动/手动执行已暂停')
    unfinished=state.db.execute("SELECT id FROM operations WHERE status IN ('prepared','moved','verified','recovery_required')").fetchall()
    if unfinished: raise SafetyError('存在未完成动作，先运行 recover')
    plan=load_plan(state,planid)
    if plan['root']!=str(Path(root).absolute()) or plan['policy_hash']!=policy.hash: raise SafetyError('根目录或规则已变化，旧计划失效')
    results=[]
    for a in plan['actions']:
        from .tasks import checkpoint
        checkpoint()
        previous=state.db.execute('SELECT status FROM operations WHERE id=?',(a['id'],)).fetchone()
        if previous and previous[0]=='committed': results.append({'source':a['source'],'status':'already_committed'}); continue
        op={**a,'run_id':planid,'policy_hash':policy.hash,'policy_version':policy.data['policy_version'],'rule_fingerprint':policy.decision_fingerprint(a.get('rule_id','')),'scope':plan['scope']}
        if a['decision'] not in ('move','review') or not a.get('target'):
            state.op(a['id'],a['decision'],op)
            if a.get('manifest'):
                iid=state.observe(a['source'],a['manifest']['kind'],a['manifest'])
                with state.db: state.add_review(iid,digest(a['manifest']),{},a.get('reason','unresolved'),'waiting_user')
            results.append({'source':a['source'],'status':a['decision'],'reason':a.get('reason','')}); continue
        stage='preflight'
        try:
            scope=plan['scope']
            if not in_scope(a['source'],scope['include_root'],scope['include_review'],scope['allow_existing']): raise SafetyError('源范围校验失败')
            src=safe_path(root,a['source'])
            if src.name not in [p.name for p in src.parent.iterdir()]: raise SafetyError('源路径必须使用真实文件名大小写，禁止隐式重命名')
            target=policy.target(str(Path(a['target']).parent).replace('\\','/'),src.name,scope['allow_archived'],a['decision']=='review')
            if target!=a['target']: raise SafetyError('文件名或目标校验失败')
            dst=safe_path(root,target)
            if dst.exists(): raise ConflictError('执行时出现同名目标')
            if manifest(src)!=a['manifest']: raise SafetyError('源内容、成员或修改时间发生变化')
            # Save all recovery material before touching any original path.
            write_json(Path(root)/'_整理规则/执行记录'/planid/(a['id']+'.prepared.json'),op)
            state.op(a['id'],'prepared',op); stage='prepared'
            dst.parent.mkdir(parents=True,exist_ok=True)
            src=safe_path(root,a['source']); dst=safe_path(root,a['target'])
            if manifest(src)!=a['manifest']: raise SafetyError('移动前源变化')
            rename_no_replace(src,dst); stage='moved'
            if crash_hook: crash_hook('after_rename',op)
            state.op(a['id'],'moved',op)
            if manifest(dst)!=a['manifest']: raise SafetyError('移动后完整性校验失败')
            state.op(a['id'],'verified',op); stage='verified'
            state.register(op)
            results.append({'source':a['source'],'target':a['target'],'status':'committed','decision':a['decision'],'files':len(a['manifest']['files'])})
        except (OSError,SafetyError) as e:
            status='recovery_required' if stage in ('moved','verified') else 'conflict' if isinstance(e,ConflictError) else 'failed'
            op['error']=str(e); state.op(a['id'],status,op)
            results.append({'source':a['source'],'status':status,'error':str(e)})
            if status=='recovery_required': break
    write_json(Path(root)/'_整理规则/执行记录'/planid/'result.json',{'run_id':planid,'at':now(),'results':results})
    held=state.get('held_sources',{})
    for a,r in zip(plan['actions'],results):
        if r['status'] in ('skip','conflict','failed') and a.get('manifest'):
            held[a['source']]={'fingerprint':digest(a['manifest']),'policy_hash':policy.hash,'status':r['status']}
        elif r['status'] in ('committed','already_committed'): held.pop(a['source'],None)
    state.set('held_sources',held)
    return {'run_id':planid,'results':results}
