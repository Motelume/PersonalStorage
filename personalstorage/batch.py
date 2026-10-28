"""Selected-only batch decisions, previewed per-file plans and bounded AI review."""
from .common import SafetyError
from .tasks import checkpoint,Cancelled
from .ai import AIManager
from .workbench import ticket,consume


def selected(desktop,body,maximum=100):
    ids=body.get('ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=maximum or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):raise SafetyError(f'请选择1至{maximum}个不同资料')
    objects=[desktop.get(i) for i in ids]
    if any(not (o.get('action') or o.get('review_id')) for o in objects):raise SafetyError('只能选择收件箱或待办中的资料')
    paths=[o['path'].casefold() for o in objects]
    if len(set(paths))!=len(paths) or any(a!=b and a.startswith(b+'/') for a in paths for b in paths):raise SafetyError('不能重复选择资料或同时选择资料包及其成员')
    return list(zip(ids,objects))


def perform(desktop,app,action,body,job):
    if action in ('wb-batch-preview','wb-batch-adopt'):
        rows=selected(desktop,body);plans=[];errors=[];targets=set()
        for ident,obj in rows:
            checkpoint()
            try:
                if action=='wb-batch-adopt':plan=desktop.perform(app,'ai-adopt',{'id':ident},job)
                else:
                    desktop.perform(app,'detail',{'id':ident},job)
                    plan=desktop.perform(app,'preview',{'ids':[ident],'target_id':body.get('target_id'),'evidence':body.get('evidence')},job)
                target=plan['actions'][0]['target'].casefold()
                if target in targets:raise SafetyError('本批另一个资料使用相同目标，保留此项待核对')
                targets.add(target);plans.append(plan)
            except Cancelled:raise
            except (OSError,SafetyError) as exc:errors.append({'source':obj['path'],'error':str(exc)})
        return {'ticket':ticket(desktop,'batch',plans),'plans':plans,'errors':errors,'files_moved':False}
    if action=='wb-batch-execute':
        plans=consume(desktop,body,'batch');ids=body.get('plan_ids')
        if not isinstance(ids,list) or not ids or len(set(ids))!=len(ids) or set(ids)-{p['id'] for p in plans}:raise SafetyError('请选择预览中要执行的项目')
        results=[]
        for plan in plans:
            if plan['id'] not in ids:continue
            checkpoint();job['phase']=f'逐项核验并归档，已完成 {len(results)} 项'
            try:results.extend(desktop.perform(app,'execute',{'plan_id':plan['id']},job)['results'])
            except Cancelled:raise
            except (OSError,SafetyError) as exc:results.append({'source':plan['actions'][0]['source'],'status':'failed','error':str(exc)})
        return {'results':results}
    if action=='wb-batch-ai-preview':
        rows=selected(desktop,body,50);common=body.get('common','');notes=body.get('notes',{})
        if not isinstance(common,str) or not common.strip() or len(common)>1500 or not isinstance(notes,dict):raise SafetyError('请输入本批共同说明（最多1500字）')
        if set(notes)-{i for i,o in rows} or any(not isinstance(v,str) or len(v)>500 for v in notes.values()):raise SafetyError('单项补充说明最多500字，且必须属于本批')
        overrides=body.get('override_ids',[])
        if not isinstance(overrides,list) or any(not isinstance(i,str) for i in overrides) or set(overrides)-{i for i,o in rows}:raise SafetyError('重新判断人工归属必须逐项明确选择')
        manager=AIManager(app,vault=desktop.credential_vault,provider_factory=desktop.ai_provider_factory);size=manager.settings()['max_items'];packets=[]
        normal=[r for r in rows if r[0] not in overrides];chunks=[normal[start:start+size] for start in range(0,len(normal),size)]+[[r] for r in rows if r[0] in overrides]
        for chunk in chunks:
            checkpoint()
            packets.append(manager.prepare([o for i,o in chunk],{o['path']:common+('\n本文件补充：'+notes[i] if notes.get(i) else '') for i,o in chunk},allow_manual_override=chunk[0][0] in overrides))
        return {'ticket':ticket(desktop,'batch-ai',packets),'batches':len(packets),'model':manager.settings()['model'],
                'documents':[doc for p in packets for doc in p['request']['documents']],'with_content':manager.settings()['with_content'],'daily_remaining':max(0,manager.settings()['daily_limit']-manager.status()['calls_today'])}
    if action=='wb-batch-ai-run':
        packets=consume(desktop,body,'batch-ai');manager=AIManager(app,vault=desktop.credential_vault,provider_factory=desktop.ai_provider_factory);results=[]
        for index,packet in enumerate(packets):
            checkpoint();job['phase']=f'AI分类 {index+1}/{len(packets)} 批'
            try:results.append({'status':'done',**manager.run(packet)})
            except Cancelled:raise
            except (OSError,SafetyError) as exc:results.append({'status':'failed','sources':[b['path'] for b in packet['bindings']],'error':str(exc)})
        return {'batches':results,'files_moved':False}
    raise SafetyError('未知批量操作')
