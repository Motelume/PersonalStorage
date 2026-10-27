"""Shared application actions for local tools; all writes require previews."""
import time
from .common import SafetyError,uid
from . import searching,backup,lifecycle,rule_package,assistance,recognition,distribution
from .rule_manager import RuleManager
from .ai import AIManager


def ticket(desktop,kind,value):
    ident=uid();desktop.previews[ident]={'workbench_kind':kind,'value':value,'at':time.monotonic()};return ident


def consume(desktop,body,kind,once=True):
    item=desktop.previews.get(body.get('ticket'),{})
    if item.get('workbench_kind')!=kind or time.monotonic()-item['at']>900:raise SafetyError('预览已过期，请重新检查')
    if once:desktop.previews.pop(body['ticket'],None)
    return item['value']


def status(app):
    return {'search':searching.status(app),'backups':backup.catalog(app),'shared_rules':rule_package.preview(app),
            'recognition':recognition.status(),'installed_versions':distribution.installed(),'resume':app.state.get('job_retries',{}),'version_requires_user_approval':True,'restore_notice':app.state.get('restore_notice')}


def perform(desktop,app,action,body,job):
    if action=='wb-index':return searching.build(app,job,body.get('budget',500))
    if action=='wb-search':
        result=searching.search(app,body.get('query'),body.get('semantic') is True)
        for row in result['rows']:row['id']=desktop.object(row['path'])
        return result
    if action=='wb-backup':
        result=backup.create_management(app);return {**result,**desktop.download(result['path'])}
    if action=='wb-restore-preview':
        value=backup.restore_preview(app,body.get('path',''));return {'ticket':ticket(desktop,'restore',value),**{k:v for k,v in value.items() if k not in ('config','stage','database_hash')}}
    if action=='wb-restore':
        result=backup.restore(app,consume(desktop,body,'restore'),body.get('with_rules') is True);desktop.scan_cache.clear();return result
    if action=='wb-original-preview':
        value=backup.original_preview(app,body.get('path',''));return {'ticket':ticket(desktop,'original-backup',value),'files':len(value['files']),'bytes':value['bytes'],'target':value['target'],'free_bytes':value['free_bytes']}
    if action=='wb-original-backup':return backup.backup_originals(app,consume(desktop,body,'original-backup'),job)
    if action=='wb-original-restore-preview':
        value=backup.original_restore_preview(app,body.get('id'),body.get('path',''));return {**value,'ticket':ticket(desktop,'original-restore',value)}
    if action=='wb-original-restore':return backup.restore_originals(app,consume(desktop,body,'original-restore'),job)
    if action=='wb-lifecycle-preview':
        value=lifecycle.preview(app,body.get('id'),body.get('status'));return {'ticket':ticket(desktop,'lifecycle',value),**{k:v for k,v in value.items() if k!='manifest'}}
    if action=='wb-lifecycle':
        result=lifecycle.apply(app,consume(desktop,body,'lifecycle'));desktop.scan_cache.clear();return result
    if action=='wb-package-preview':
        value=rule_package.preview(app);return {**value,'ticket':ticket(desktop,'package',value)}
    if action=='wb-package-update':
        value=consume(desktop,body,'package')
        if rule_package.preview(app)!=value:raise SafetyError('通用规则更新预览已变化')
        result=rule_package.update(app);desktop.scan_cache.clear();return result
    if action=='wb-ai-preview':
        manager=AIManager(app,vault=desktop.credential_vault,provider_factory=desktop.ai_provider_factory)
        packet=assistance.prepare(app,manager,body.get('kind'),body.get('goal'))
        return {'ticket':ticket(desktop,'ai-proposal',packet),'request':packet['request'],'kind':packet['kind'],'model':packet['settings']['model']}
    if action=='wb-ai-run':
        packet=consume(desktop,body,'ai-proposal');manager=AIManager(app,vault=desktop.credential_vault,provider_factory=desktop.ai_provider_factory)
        result=assistance.run(app,manager,packet);return {**result,'ticket':ticket(desktop,'proposal-result',{'result':result,'policy_hash':app.policy.hash})}
    if action=='wb-ai-accept-rule':
        value=consume(desktop,body,'proposal-result',False)
        if value['policy_hash']!=app.policy.hash:raise SafetyError('规则已变化，请重新生成候选')
        row=value['result'].get('rules',[])[body.get('index',0)];manager=RuleManager(app);status=manager.status();draft=status['draft'];data=(draft or {}).get('model') or status['live']
        if any(r['rule']['id']==row['rule']['id'] for r in data['rules']):raise SafetyError('该候选已加入草稿')
        data['rules'].append(row);return manager.save(data,(draft or {}).get('revision'),status['policy_hash'])
    if action=='wb-ai-accept-link':
        from . import overview
        value=consume(desktop,body,'proposal-result',False);row=value['result'].get('links',[])[body.get('index',0)]
        def node(t):return {'path':t['path'],'overview':True,'node_identity':t['identity']}
        return overview.save_link(app.root,app.state,node(row['source']),node(row['target']),{'label':row['reason'][:60],'note':'经用户确认的AI关联建议'})
    if action=='wb-recognition-status':return recognition.status()
    if action=='wb-recognition-location':
        from .backup import destination
        return recognition.configure_location(destination(app,body.get('path','')))
    if action=='wb-recognition-install':
        if body.get('confirmed') is not True:raise SafetyError('首次准备组件需要明确确认下载')
        return recognition.install(body.get('kind'),job)
    if action=='wb-recognize':
        from .common import manifest,digest
        from .inboxes import resolve
        obj=desktop.get(body.get('id'))
        if not obj.get('rule_browser') or obj.get('source_root'):raise SafetyError('请选择受控样本文件')
        path=resolve(app.root,app.state,obj['path']);st=path.stat()
        if not path.is_file() or [st.st_dev,st.st_ino]!=obj['identity'] or st.st_size>1024*1024*1024:raise SafetyError('识别样本无效、已变化或超过1GiB')
        before=manifest(path);result=recognition.infer(body.get('kind'),path)
        if manifest(path)!=before:raise SafetyError('识别期间原文件变化，旧结果未采用')
        return {**result,'original_files_touched':False,'ticket':ticket(desktop,'recognition-result',{'path':obj['path'],'manifest':before,'result':result})}
    if action=='wb-recognition-accept':
        from .inboxes import resolve
        from .common import manifest
        value=consume(desktop,body,'recognition-result');path=resolve(app.root,app.state,value['path'])
        if manifest(path)!=value['manifest']:raise SafetyError('样本已变化，请重新识别')
        result=recognition.accept(path,value['result']);desktop.scan_cache.clear()
        with app.state.db:app.state.db.execute('DELETE FROM ai_location_cache WHERE path=?',(value['path'],))
        return result
    if action=='wb-update-check':return distribution.check()
    if action=='wb-update-stage':
        value=distribution.stage(app,consume(desktop,body,'update-release'));return {**value,'ticket':ticket(desktop,'update-installer',value)}
    if action=='wb-update-preview':
        result=distribution.check()
        if not result['available']:return result
        return {**result,'ticket':ticket(desktop,'update-release',result['latest'])}
    if action=='wb-update-launch':
        value=consume(desktop,body,'update-installer');return distribution.launch(value)
    if action=='wb-rollback-preview':
        value=distribution.rollback_preview(app,body.get('version'));return {**value,'ticket':ticket(desktop,'rollback',value)}
    if action=='wb-rollback':return distribution.rollback(app,consume(desktop,body,'rollback'))
    if action=='wb-quality':
        from .quality import evaluate
        return evaluate(app,body.get('cases'),job)
    if action=='wb-diagnostics':
        from . import ocr
        import shutil
        return {'database_integrity':app.state.db.execute('PRAGMA quick_check').fetchone()[0],'ocr_ready':ocr.availability()['ready'],'free_state_bytes':shutil.disk_usage(app.state.profile).free,'policy_hash':app.policy.hash,'unfinished_operations':app.state.db.execute("SELECT COUNT(*) FROM operations WHERE status IN ('prepared','moved','verified','recovery_required')").fetchone()[0],'private_data_exported':False}
    raise SafetyError('未知工作台操作')
