"""Verified, no-overwrite external inbox COPY; originals are always retained."""
from pathlib import Path
import json
import os
import shutil
from .common import SafetyError,manifest,digest,uid,now,safe_path,instance_lock,rename_no_replace,write_json
from . import inboxes
from .scanning import metadata

def build(app,objects,*,target_parent=None,evidence=''):
    actions=[];targets=set()
    for obj in objects:
        source=obj['path']
        if not source.startswith(inboxes.PREFIX):raise SafetyError('外部复制计划不能混入库内移动')
        path=inboxes.resolve(app.root,app.state,source)
        if obj.get('scan_fingerprint') and metadata(path)['fingerprint']!=obj['scan_fingerprint']:raise SafetyError('外部资料已变化，请重新扫描')
        m=manifest(path);parent=target_parent or obj.get('action',{}).get('target_parent')
        if not parent:raise SafetyError('尚无确定归属，请先让AI分类或选择目标')
        target=app.policy.target(parent,path.name)
        if target in targets or safe_path(app.root,target).exists():raise SafetyError('库内目标同名，不能覆盖或自动改名')
        targets.add(target)
        if obj.get('fingerprint') and obj['fingerprint']!=digest(m):raise SafetyError('资料版本已变化')
        actions.append({'id':uid(),'source':source,'source_absolute':str(path),'target':target,'target_parent':parent,'manifest':m,'manifest_hash':digest(m),
                        'source_metadata':metadata(path)['fingerprint'],'decision':'move','operation_type':'external_copy','source_retained':True,
                        'classification_verified':bool(target_parent or obj.get('action',{}).get('classification_verified')),'ai_classified':bool(obj.get('action',{}).get('ai_classified')),
                        'rule_id':'external_import','evidence':evidence or obj.get('action',{}).get('evidence','用户确认外部资料归档'),
                        'manual':bool(target_parent),'rule_fingerprint':digest(['external_copy',parent])})
    plan={'id':uid(),'type':'external_copy','created_at':now(),'root':str(app.root),'policy_hash':app.policy.hash,'policy_version':app.policy.data['policy_version'],
          'scope':{'include_root':False,'include_review':False,'allow_existing':False,'allow_archived':False},'actions':actions}
    with app.state.db:app.state.db.execute('INSERT INTO plans VALUES(?,?)',(plan['id'],json.dumps(plan,ensure_ascii=False)))
    return plan

def execute(app,plan):
    if plan['policy_hash']!=app.policy.hash:raise SafetyError('规则已变化，请重新预览')
    results=[]
    with instance_lock(app.state.profile):
        if app.state.get('paused',False):raise SafetyError('执行已暂停')
        if app.state.db.execute("SELECT COUNT(*) FROM operations WHERE status IN ('prepared','moved','verified','recovery_required')").fetchone()[0]:raise SafetyError('请先核对未完成操作')
        for a in plan['actions']:
            op={**a,'run_id':plan['id'],'policy_hash':app.policy.hash,'policy_version':app.policy.data['policy_version'],'scope':plan['scope']}
            published=False
            try:
                source=inboxes.resolve(app.root,app.state,a['source']);target=safe_path(app.root,a['target'])
                if manifest(source)!=a['manifest']:raise SafetyError('外部资料已变化，请重新预览')
                if app.policy.target(a['target_parent'],source.name)!=a['target'] or target.exists():raise SafetyError('归档目标已占用或不匹配')
                stage=safe_path(app.root,'_整理规则/导入暂存/'+a['id']+'/'+source.name)
                if stage.parent.exists():raise SafetyError('导入暂存已存在，请检查恢复记录')
                stage.parent.mkdir(parents=True)
                state=app.state;state.op(a['id'],'prepared',op)
                if source.is_dir():shutil.copytree(source,stage,copy_function=shutil.copy2,symlinks=True)
                else:
                    with source.open('rb') as src,stage.open('xb') as dst:shutil.copyfileobj(src,dst,4*1024*1024);dst.flush();os.fsync(dst.fileno())
                    shutil.copystat(source,stage)
                if manifest(stage)!=a['manifest'] or manifest(source)!=a['manifest']:raise SafetyError('复制后校验不一致，原件保留')
                target.parent.mkdir(parents=True,exist_ok=True);target=safe_path(app.root,a['target'])
                rename_no_replace(stage,target);published=True;state.op(a['id'],'moved',op)
                if manifest(target)!=a['manifest']:raise SafetyError('归档副本校验失败，请核对记录；原件仍保留')
                state.register(op)
                mark={'target':a['target'],'source_metadata':metadata(source)['fingerprint'],'target_metadata':metadata(target)['fingerprint'],'version':a['manifest_hash'],'at':now()}
                with state.db:state.db.execute('INSERT OR REPLACE INTO inbox_imports VALUES(?,?)',(a['source'],json.dumps(mark,ensure_ascii=False)))
                results.append({'source':a['source'],'target':a['target'],'status':'committed','source_retained':True,'operation_type':'external_copy'})
            except (OSError,SafetyError) as exc:
                app.state.op(a['id'],'recovery_required' if published else 'failed',{**op,'error':str(exc)})
                results.append({'source':a['source'],'status':'recovery_required' if published else 'failed','error':str(exc),'source_retained':True})
            # Only remove this generated staging directory, never source or target.
            temp_parent=safe_path(app.root,'_整理规则/导入暂存/'+a['id'])
            if temp_parent.exists():
                expected=safe_path(app.root,'_整理规则/导入暂存')
                if temp_parent.resolve().is_relative_to(expected.resolve()):
                    try:shutil.rmtree(temp_parent)
                    except OSError:app.state.set('log_export_warning','归档已记账；部分生成的导入暂存需稍后清理')
    return {'run_id':plan['id'],'results':results,'source_files_deleted':False}
