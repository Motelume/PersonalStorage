"""Verified external inbox imports with explicit per-source copy/move policy."""
import json
import os
import shutil
from .common import SafetyError,manifest,digest,uid,now,safe_path,instance_lock,rename_no_replace
from . import inboxes,transfer
from .scanning import metadata

def check_mode(app,action):
    ident=action['source'].split('/')[1];record=inboxes.source_record(app.state,ident)
    if inboxes.mode(app.state,ident)!=action.get('import_mode','copy') or record.get('mode_revision')!=action.get('mode_revision'):
        raise SafetyError('收件箱归档方式已改变，请重新预览')

def copy_member(source,target):
    # Flush every member of a package, not just standalone files.
    with open(source,'rb') as src,open(target,'xb') as dst:
        shutil.copyfileobj(src,dst,4*1024*1024);dst.flush();os.fsync(dst.fileno())
    shutil.copystat(source,target)
    return target

def build(app,objects,*,target_parent=None,evidence=''):
    actions=[];targets=set()
    for obj in objects:
        source=obj['path']
        if not source.startswith(inboxes.PREFIX):raise SafetyError('外部导入计划不能混入库内移动')
        path=inboxes.resolve(app.root,app.state,source);ident=source.split('/')[1]
        record=inboxes.source_record(app.state,ident);mode=inboxes.mode(app.state,ident)
        if obj.get('scan_fingerprint') and metadata(path)['fingerprint']!=obj['scan_fingerprint']:raise SafetyError('外部资料已变化，请重新扫描')
        m=manifest(path);parent=target_parent or obj.get('action',{}).get('target_parent')
        if not parent:raise SafetyError('尚无确定归属，请先让AI分类或选择目标')
        target=app.policy.target(parent,path.name);destination=safe_path(app.root,target);reuse=False
        if destination.exists() and mode=='move':
            mark=app.state.db.execute('SELECT data FROM inbox_imports WHERE source=?',(source,)).fetchone()
            proof=json.loads(mark['data']) if mark else {}
            reuse=proof.get('target')==target and proof.get('version')==digest(m) and manifest(destination)==m
        if target.casefold() in targets or (destination.exists() and not reuse):raise SafetyError('库内目标同名，不能覆盖或自动改名')
        targets.add(target.casefold())
        if obj.get('fingerprint') and obj['fingerprint']!=digest(m):raise SafetyError('资料版本已变化')
        actions.append({'id':uid(),'source':source,'source_absolute':str(path),'target':target,'target_parent':parent,'manifest':m,'manifest_hash':digest(m),
            'source_metadata':metadata(path)['fingerprint'],'source_identity':transfer.identity(path),'import_mode':mode,'mode_revision':record.get('mode_revision'),
            'decision':'move','operation_type':'external_'+mode,'source_retained':mode=='copy','reuse_existing':reuse,
            'classification_verified':bool(target_parent or obj.get('action',{}).get('classification_verified')),'ai_classified':bool(obj.get('action',{}).get('ai_classified')),
            'rule_id':'external_import','evidence':evidence or obj.get('action',{}).get('evidence','用户确认外部资料归档'),
            'manual':bool(target_parent),'rule_fingerprint':digest(['external_import',parent])})
    plan={'id':uid(),'type':'external_import','created_at':now(),'root':str(app.root),'policy_hash':app.policy.hash,'policy_version':app.policy.data['policy_version'],
          'scope':{'include_root':False,'include_review':False,'allow_existing':False,'allow_archived':False},'actions':actions}
    with app.state.db:app.state.db.execute('INSERT INTO plans VALUES(?,?)',(plan['id'],json.dumps(plan,ensure_ascii=False)))
    return plan

def finalize(app,op,retained,error=None):
    """Register a verified archive once; retries only update cleanup state."""
    target=safe_path(app.root,op['target'])
    if manifest(target)!=op['manifest']:raise SafetyError('归档副本已变化，请人工核对')
    op.update(source_retained=retained,cleanup_complete=not retained if op['operation_type']=='external_move' else None)
    if error:op['error']=str(error)
    else:op.pop('error',None)
    status='source_retained' if op['operation_type']=='external_move' and retained else 'committed'
    if not op.get('target_registered'):
        op.update(target_registered=True,completion_status=status)
        app.state.register(op)
    app.state.op(op['id'],status,op)
    with app.state.db:app.state.db.execute('DELETE FROM inbox_imports WHERE source=?',(op['source'],))
    if retained:
        try:
            source=inboxes.resolve(app.root,app.state,op['source'])
            if manifest(source)==op['manifest']:
                mark={'target':op['target'],'source_metadata':metadata(source)['fingerprint'],'target_metadata':metadata(target)['fingerprint'],'version':digest(op['manifest']),'at':now()}
                with app.state.db:app.state.db.execute('INSERT OR REPLACE INTO inbox_imports VALUES(?,?)',(op['source'],json.dumps(mark,ensure_ascii=False)))
        except (OSError,SafetyError):pass
    return {'operation_id':op['id'],'source':op['source'],'target':op['target'],'status':status,'source_retained':retained,'operation_type':op['operation_type'],**({'error':str(error)} if error else {})}

def execute(app,plan,*,crash_hook=None):
    if plan['policy_hash']!=app.policy.hash:raise SafetyError('规则已变化，请重新预览')
    results=[]
    with instance_lock(app.state.profile):
        if app.state.get('paused',False):raise SafetyError('执行已暂停')
        if app.state.db.execute("SELECT COUNT(*) FROM operations WHERE status IN ('prepared','moved','verified','recovery_required')").fetchone()[0]:raise SafetyError('请先核对未完成操作')
        for a in plan['actions']:
            previous=app.state.db.execute('SELECT status FROM operations WHERE id=?',(a['id'],)).fetchone()
            if previous:raise SafetyError('这个导入计划已执行，请重新扫描或在操作记录中核对')
            op={**a,'run_id':plan['id'],'policy_hash':app.policy.hash,'policy_version':app.policy.data['policy_version'],'scope':plan['scope']}
            published=False
            try:
                check_mode(app,a);source=inboxes.resolve(app.root,app.state,a['source']);target=safe_path(app.root,a['target'])
                if manifest(source)!=a['manifest'] or transfer.identity(source)!=a['source_identity']:raise SafetyError('外部资料已变化，请重新预览')
                if app.policy.target(a['target_parent'],source.name)!=a['target']:raise SafetyError('归档目标不匹配')
                app.state.op(a['id'],'prepared',op)
                if a.get('reuse_existing'):
                    proof=app.state.db.execute('SELECT data FROM inbox_imports WHERE source=?',(a['source'],)).fetchone()
                    proof=json.loads(proof['data']) if proof else {}
                    if proof.get('target')!=a['target'] or proof.get('version')!=a['manifest_hash'] or manifest(target)!=a['manifest']:raise SafetyError('已归档副本的校验证明已失效')
                    published=True;op['target_registered']=True
                else:
                    if target.exists():raise SafetyError('归档目标已占用')
                    stage=safe_path(app.root,'_整理规则/导入暂存/'+a['id']+'/'+source.name)
                    if stage.parent.exists():raise SafetyError('导入暂存已存在，请检查恢复记录')
                    stage.parent.mkdir(parents=True)
                    if source.is_dir():shutil.copytree(source,stage,copy_function=copy_member,symlinks=True)
                    else:copy_member(source,stage)
                    if manifest(stage)!=a['manifest'] or manifest(source)!=a['manifest']:raise SafetyError('复制后校验不一致，原件保留')
                    check_mode(app,a);target.parent.mkdir(parents=True,exist_ok=True);target=safe_path(app.root,a['target'])
                    rename_no_replace(stage,target);published=True
                app.state.op(a['id'],'moved',op)
                if manifest(target)!=a['manifest']:raise SafetyError('归档副本校验失败，请核对记录')
                if crash_hook:crash_hook('after_target_verified',op)
                if a['import_mode']=='move':
                    app.state.op(a['id'],'verified',op)
                    transfer.cleanup(app,op,crash_hook=crash_hook)
                results.append(finalize(app,op,a['import_mode']=='copy'))
            except (OSError,SafetyError) as exc:
                status='recovery_required' if published else 'failed'
                if published:
                    try:
                        results.append(finalize(app,op,True,exc));continue
                    except (OSError,SafetyError):pass
                app.state.op(a['id'],status,{**op,'error':str(exc),'source_retained':True})
                results.append({'source':a['source'],'status':status,'error':str(exc),'source_retained':True})
            finally:
                # Only this generated staging directory; never a source directory.
                temp_parent=safe_path(app.root,'_整理规则/导入暂存/'+a['id']);expected=safe_path(app.root,'_整理规则/导入暂存')
                if temp_parent.exists() and temp_parent.resolve().is_relative_to(expected.resolve()):
                    try:shutil.rmtree(temp_parent)
                    except OSError:app.state.set('log_export_warning','部分生成的导入暂存需稍后清理')
    return {'run_id':plan['id'],'results':results,'source_files_deleted':any(r.get('operation_type')=='external_move' and not r['source_retained'] for r in results)}

def cleanup_preview(app,operation_id):
    row=app.state.db.execute("SELECT data FROM operations WHERE id=? AND status='source_retained'",(operation_id,)).fetchone()
    if not row:raise SafetyError('请选择原件未完全清理的转移记录')
    op=json.loads(row['data'])
    if op.get('operation_type')!='external_move':raise SafetyError('该操作不是转移归档')
    source=inboxes.resolve(app.root,app.state,op['source']);ident=op['source'].split('/')[1]
    if inboxes.mode(app.state,ident)!='move':raise SafetyError('请先将该来源设为转移归档')
    if transfer.identity(source)!=op['source_identity']:raise SafetyError('源资料身份已改变，不能自动清理')
    if manifest(safe_path(app.root,op['target']))!=op['manifest']:raise SafetyError('归档副本已变化，不能清理原件')
    op['mode_revision']=inboxes.source_record(app.state,ident).get('mode_revision')
    expected=transfer.expected_remainder(source,op['manifest'])
    return {'op':op,'expected':expected,'policy_hash':app.policy.hash,'source_absolute':str(source),'target':op['target'],'file_count':len(expected['files'])}

def cleanup_execute(app,ticket):
    with instance_lock(app.state.profile):
        if app.state.get('paused',False):raise SafetyError('执行已暂停')
        if ticket['policy_hash']!=app.policy.hash:raise SafetyError('规则已改变，请重新核对')
        op=ticket['op'];check_mode(app,op)
        app.state.op(op['id'],'verified',op)
        try:
            transfer.cleanup(app,op,ticket['expected'])
            return finalize(app,op,False)
        except (OSError,SafetyError) as exc:
            try:return finalize(app,op,True,exc)
            except (OSError,SafetyError):
                app.state.op(op['id'],'recovery_required',{**op,'error':str(exc)});raise
