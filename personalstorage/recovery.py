from pathlib import Path
import json
from .common import safe_path,manifest,instance_lock,rename_no_replace,uid,digest,SafetyError,write_json,read_json

def recover(root,state,policy):
    out=[]
    with instance_lock(state.profile):
        rows=state.db.execute("SELECT * FROM operations WHERE status IN ('prepared','moved','verified','recovery_required')").fetchall()
        for row in rows:
            op=json.loads(row['data'])
            try:
                if op.get('operation_type') in ('external_copy','external_move'):
                    from types import SimpleNamespace
                    from . import inboxes,importing
                    dst=safe_path(root,op['target'])
                    if dst.exists() and manifest(dst)==op['manifest']:
                        # Recovery reconciles only: source deletion always needs
                        # a fresh, explicit cleanup preview and confirmation.
                        retained=True
                        if op['operation_type']=='external_move':
                            try:retained=inboxes.resolve(root,state,op['source'],enabled=False).exists()
                            except (OSError,SafetyError):retained=True
                        result=importing.finalize(SimpleNamespace(root=root,state=state),op,retained,'操作中断，原件状态需核对' if retained and op['operation_type']=='external_move' else None)
                        status=result['status']
                    elif not dst.exists():state.op(op['id'],'failed',{**op,'error':'归档副本未发布，外部原件保留'});status='source_retained'
                    else:state.op(op['id'],'recovery_required',{**op,'error':'副本状态需核对；不会删除外部原件'});status='manual_required'
                    out.append({'operation_id':op['id'],'status':status});continue
                src=safe_path(root,op['source']); dst=safe_path(root,op['target'])
                if not src.exists() and dst.exists() and manifest(dst)==op['manifest']:
                    # Recovery only reconciles an already completed move, never executes a new move.
                    state.register(op); status='committed'
                elif src.exists() and not dst.exists() and manifest(src)==op['manifest']:
                    state.op(op['id'],'failed',{**op,'error':'未执行移动；重新生成计划后再运行'}); status='source_retained'
                else:
                    state.op(op['id'],'recovery_required',{**op,'error':'源目标状态不唯一或内容不符，需人工核对'}); status='manual_required'
            except (OSError,SafetyError) as e:
                status='manual_required'; state.op(op['id'],'recovery_required',{**op,'error':str(e)})
            out.append({'operation_id':op['id'],'status':status})
    return out

def undo(root,state,policy,operation_id):
    from .executor import execute
    row=state.db.execute("SELECT * FROM operations WHERE id=? AND status='committed'",(operation_id,)).fetchone()
    if not row: raise SafetyError('只能撤销已完成操作')
    op=json.loads(row['data']); src=safe_path(root,op['target']); dst=safe_path(root,op['source'])
    with instance_lock(state.profile):
        if state.get('paused',False): raise SafetyError('执行已暂停')
        if dst.exists(): raise SafetyError('原位置已被占用，不能撤销')
        if manifest(src)!=op['manifest']: raise SafetyError('文件移动后已变化，不能自动撤销')
        # Undo is an explicit user action to a previously logged original location.
        from .planner import in_scope
        if not in_scope(op['source'],True,True,op['scope'].get('allow_existing',False)): raise SafetyError('原位置不在允许范围')
        ident=uid(); run=uid()
        rev={**op,'id':ident,'run_id':run,'source':op['target'],'target':op['source'],'rule_id':'explicit_undo','undo_of':operation_id}
        write_json(Path(root)/'_整理规则/执行记录'/run/(ident+'.prepared.json'),rev)
        state.op(ident,'prepared',rev)
        dst.parent.mkdir(parents=True,exist_ok=True)
        safe_path(root,rev['source']); safe_path(root,rev['target'])
        rename_no_replace(src,dst)
        state.op(ident,'moved',rev)
        if manifest(dst)!=op['manifest']: raise SafetyError('撤销校验异常，运行recover')
        state.register(rev)
        return {'operation_id':ident,'status':'committed','source':rev['source'],'target':rev['target']}
