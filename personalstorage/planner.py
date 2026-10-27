from pathlib import Path
from .common import safe_path,relpath,manifest,digest,now,uid,write_json,SafetyError
from .discovery import discover,stability
from .extraction import extract

def in_scope(source,include_root=False,include_review=False,allow_existing=False):
    source=relpath(source)
    if source.split('/')[-1].casefold()=='agents.md' or source.startswith(('_整理规则/','09_移动硬盘管理/')): return False
    if source.startswith('99_临时待整理/'):return include_review and allow_existing
    if source.startswith('00_收件箱/待确认/'): return include_review
    if source=='00_收件箱/待确认': return False
    if source.startswith('00_收件箱/'): return True
    if '/' not in source: return include_root
    return allow_existing and source.split('/')[0] in ('01_课程学习','02_项目','03_竞赛','04_学生工作与实践','05_升学与交流','06_资料库','07_个人资料','08_软件与工具')

def build_plan(root,state,policy,*,include_root=False,include_review=False,sources=None,decisions=None,approve_decisions=False,allow_existing=False,allow_archived=False,stable_seconds=1,context=None,automatic=False):
    root=Path(root)
    selected=sources if sources is not None else discover(root,include_root,include_review)
    if decisions and not approve_decisions: raise SafetyError('外部归属决定须显式 --approve-decisions；不能把普通文件当授权')
    decisions=decisions or {}
    normalized=[relpath(x) for x in selected]
    if len({x.casefold() for x in normalized})!=len(normalized): raise SafetyError('重复源')
    for a in normalized:
        if any(b!=a and b.casefold().startswith(a.casefold()+'/') for b in normalized): raise SafetyError('不能同时处理目录及内部成员')
    plan={'id':uid(),'created_at':now(),'root':str(root.absolute()),'policy_hash':policy.hash,'policy_version':policy.data['policy_version'],'scope':{'include_root':include_root,'include_review':include_review,'allow_existing':allow_existing,'allow_archived':allow_archived},'actions':[]}
    targets=set()
    for source in normalized:
        a={'id':uid(),'source':source,'decision':'skip'}
        try:
            if not in_scope(source,include_root,include_review,allow_existing): raise SafetyError('源不在本次授权范围')
            p=safe_path(root,source); stability(p,stable_seconds)
            m=manifest(p); a['manifest']=m; a['manifest_hash']=digest(m)
            if not m['files']: raise SafetyError('空文件夹保持原位')
            features=extract(p)
            d=policy.classify(features,context)
            manual=decisions.get(source)
            if manual:
                if manual.get('manifest_hash')!=digest(m): raise SafetyError('归属决定绑定的文件版本已变化')
                if not manual.get('evidence'): raise SafetyError('归属决定必须写明依据')
                d={'decision':'move','target_parent':manual['target_parent'],'rule_id':'authorized_manual','evidence':manual['evidence']}
                a['manual']=True
            if d['decision']=='review':
                a.update({'decision':'review','reason':d['reason'],'candidates':d.get('candidates',[]),'rule_id':'unresolved','evidence':d['reason']})
                if source.startswith(policy.data['review_path']+'/'): a['decision']='skip'
                else: a['target']=policy.target(policy.data['review_path'],p.name,review=True)
            else:
                a.update(d); a['target']=policy.target(d['target_parent'],p.name,allow_archived,review=False)
            if a.get('target')==source:
                a['decision']='skip'; a['reason']='已在目标位置'
            elif a.get('target'):
                key=a['target'].casefold()
                if key in targets or safe_path(root,a['target']).exists():
                    a['decision']='conflict'; a['reason']='目标同名或本批目标重名，保留源原位'
                targets.add(key)
        except (OSError,SafetyError) as e: a.update({'decision':'skip','reason':str(e)})
        if automatic:
            held=state.get('held_sources',{}).get(source)
            if held and a.get('manifest') and held.get('fingerprint')==digest(a['manifest']) and held.get('policy_hash')==policy.hash:
                if held.get('status')!='conflict' or (a.get('target') and safe_path(root,a['target']).exists()): continue
        plan['actions'].append(a)
    if automatic and not plan['actions']: return plan
    record=root/'_整理规则/执行记录'/plan['id']
    write_json(record/'plan.json',plan)
    write_json(record/'source_manifest.json',[{'source':a['source'],'manifest_hash':a.get('manifest_hash'),'manifest':a.get('manifest')} for a in plan['actions']])
    with state.db: state.db.execute('INSERT INTO plans VALUES(?,?)',(plan['id'],__import__('json').dumps(plan,ensure_ascii=False)))
    return plan
