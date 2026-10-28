"""Version-bound reference sets, read-only AI comparisons and testable rule drafts."""
import json
from pathlib import Path
from .common import SafetyError,manifest,digest,uid,now,safe_path,instance_lock
from .extraction import extract
from .tasks import checkpoint
from .ai import AIManager
from .policy import Policy


def sample(app,limit=40):
    from .searching import files
    if not isinstance(limit,int) or not 5<=limit<=100:raise SafetyError('样本数量应为5至100')
    groups={}
    for p in files(app.root):
        relative=p.relative_to(app.root).as_posix()
        if relative.startswith(('00_','07_','08_','99_')) or p.suffix.lower() not in ('.pdf','.docx','.doc','.pptx','.txt','.md'):continue
        groups.setdefault((relative.split('/')[0],p.suffix.lower()),[]).append(relative)
    for group in groups.values():group.sort(key=digest)
    result=[]
    while groups and len(result)<limit:
        for key in sorted(list(groups)):
            result.append({'path':groups[key].pop(0),'expected_target':None,'label_status':'unlabelled'})
            if not groups[key]:groups.pop(key)
            if len(result)>=limit:break
    return {'samples':result,'at':now(),'ground_truth':'unlabelled','note':'分层抽样只生成待标注清单；所在目录不自动作为正确答案'}


def prepare(app,manager,cases,reference_source):
    if not isinstance(cases,list) or not 1<=len(cases)<=50:raise SafetyError('对比每次1至50项已标注资料')
    if reference_source not in ('user_supplied_labels','assistant_reviewed_reference'):raise SafetyError('请明确标签来源')
    settings=manager.settings()
    if not settings['enabled'] or not manager.vault.configured():raise SafetyError('请先启用AI并配置密钥')
    if len({c['path'] for c in cases})!=len(cases):raise SafetyError('样本不能重复')
    documents=[];bindings=[]
    for case in cases:
        checkpoint();p=safe_path(app.root,case['path'])
        if case['path'].startswith(('_整理规则/','09_')) or not p.is_file():raise SafetyError('请选择库内普通文件')
        expected=case.get('expected_target')
        if expected:app.policy.target(expected,p.name)
        before=manifest(p);f=extract(p,limit=4000)
        if manifest(p)!=before:raise SafetyError('读取期间文件变化')
        ident=uid();documents.append({'id':ident,'name':p.name,'source':case['path'],'text':f.get('text','') if settings['with_content'] else '', 'members':[], 'user_context':'', 'current_assignment':{},'audit_mode':False,'user_requested_reclassification':False})
        bindings.append({'id':ident,'path':case['path'],'manifest':before,'expected_target':expected,'rule_result':app.policy.classify(f)})
    for document in documents:document['source']=document['name']
    targets=manager.targets(documents)
    # Correct labels are deliberately absent from the model input.
    return {'id':uid(),'at':now(),'settings':settings,'policy_hash':app.policy.hash,'documents':documents,'targets':targets,'bindings':bindings,'reference_source':reference_source}


def compare(app,manager,packet,job=None):
    with instance_lock(app.state.profile):
        if manager.settings()!=packet['settings'] or Policy(app.root).hash!=packet['policy_hash']:raise SafetyError('设置或规则变化，请重新预览')
        for b in packet['bindings']:
            if manifest(safe_path(app.root,b['path']))!=b['manifest']:raise SafetyError('样本变化，请重新预览')
        rows=[];usage={};targets={t['id']:t['path'] for t in packet['targets']};size=manager.settings()['max_items']
        for start in range(0,len(packet['documents']),size):
            checkpoint()
            if manager.status()['calls_today']>=manager.settings()['daily_limit']:raise SafetyError('达到每日上限；已完成对比保存在评估历史，不自动重试')
            provider=manager.provider_factory(manager.vault.read(),model=manager.settings()['model']);record={'id':uid(),'at':now(),'kind':'quality_comparison','status':'failed','items':len(packet['documents'][start:start+size]),'usage':{}}
            batch_start=len(rows)
            try:
                docs=packet['documents'][start:start+size]
                answer=provider.review({'policy':{k:app.policy.data[k] for k in ('categories','classification_rules','subcategory_rules','explicit_preferences') if k in app.policy.data},'candidate_targets':packet['targets'],'documents':docs})
                if manager.settings()!=packet['settings'] or Policy(app.root).hash!=packet['policy_hash']:raise SafetyError('评估期间规则或设置变化，未采用本批结果')
                answers=answer.get('items',[])
                if len(answers)!=len(docs) or {a.get('id') for a in answers}!={d['id'] for d in docs}:raise SafetyError('AI结果与样本不匹配')
                for a in answers:
                    b=next(x for x in packet['bindings'] if x['id']==a['id']);decision=a.get('decision')
                    if decision not in ('needs_user_input','suggest_reclassify','confirm'):raise SafetyError('AI决定格式无效')
                    target=targets.get(a.get('target_id')) if decision=='suggest_reclassify' else None
                    if decision=='suggest_reclassify' and target is None:raise SafetyError('AI返回未知目录')
                    if manifest(safe_path(app.root,b['path']))!=b['manifest']:raise SafetyError('评估期间文件变化')
                    rows.append({'path':b['path'],'expected_target':b['expected_target'],'rule_result':b['rule_result'],'ai_decision':decision,'ai_target':target,'candidate_present':b['expected_target'] in targets.values(),'ai_reason':str(a.get('reason',''))[:1000],'ai_correct':target==b['expected_target'] if b['expected_target'] else decision=='needs_user_input'})
                record['status']='completed'
            except BaseException:
                rows=rows[:batch_start];raise
            finally:
                record['usage']=getattr(provider,'usage',{});counts=app.state.get('ai_daily_counts',{});day=record['at'][:10];counts[day]=manager.status()['calls_today']+1;app.state.set('ai_daily_counts',counts)
                app.state.set('ai_history',(app.state.get('ai_history',[])+[record])[-200:])
                for k,v in record['usage'].items():usage[k]=usage.get(k,0)+v
                app.state.set('quality_comparison_progress',{'at':now(),'rows':rows,'usage':usage,'reference_source':packet['reference_source'],'complete':False})
            if job is not None:job['phase']=f'只读对比已完成 {len(rows)}/{len(packet["bindings"])} 项'
        result={'at':now(),'rows':rows,'samples':len(rows),'reference_source':packet['reference_source'],'ai_accuracy':sum(r['ai_correct'] for r in rows)/len(rows),'rule_accuracy':sum((r['rule_result'].get('target_parent')==r['expected_target']) if r['expected_target'] else r['rule_result']['decision']=='review' for r in rows)/len(rows),'ai_review_fraction':sum(r['ai_decision']=='needs_user_input' for r in rows)/len(rows),'usage':usage,'files_moved':False,'complete':True}
        result.update(candidate_coverage=sum(r['candidate_present'] for r in rows)/len(rows),wrong_auto_suggestions=sum(r['ai_target'] is not None and not r['ai_correct'] for r in rows))
        app.state.set('quality_comparison',result);return result


def rule_candidates(app,cases):
    """Exact names AND content evidence; no filename-only broad automation."""
    groups={}
    for c in cases:
        if not c.get('expected_target'):continue
        p=safe_path(app.root,c['path']);f=extract(p,limit=4000)
        import re
        terms=re.findall(r'[A-Za-z]{5,}|[\u4e00-\u9fff]{4,12}',f.get('text',''))
        words=[x for x in terms if x.lower() not in ('university','china','copyright','contents','national','college','students') and not re.search('大学|学院|参考答案|课程名称|评分|试卷',x)]
        if not words:continue
        key=c['expected_target'];groups.setdefault(key,[]).append((p.name,words[0]))
    result=[]
    for target,entries in groups.items():
        names=sorted({x[0] for x in entries});terms=sorted({x[1] for x in entries})
        rule={'id':'reviewed_'+uid()[:10],'when':{'all':[{'field':'name','op':'in','value':names},{'any':[{'field':'text','op':'contains','value':w} for w in terms]}]},'target_parent':target,'level':100}
        Policy.from_data(app.root,{**app.policy.data,'executable_rules':[rule]})
        result.append({'rule':rule,'enabled':False,'title':'已复核资料：'+target.split('/')[-1],'note':'精确文件名与正文双证据候选；必须试跑、核对负例后发布，不代表泛化准确率'})
    return result
