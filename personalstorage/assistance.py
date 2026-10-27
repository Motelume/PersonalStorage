"""Explicitly previewed AI rule/link proposals. Acceptance never publishes rules."""
import copy
import re
from .common import SafetyError,digest,now,uid,safe_path,instance_lock
from .policy import Policy

def clean(value,limit):return re.sub(r'\bsk-[A-Za-z0-9_-]{10,}\b','[密钥已隐藏]',str(value))[:limit]


def prepare(app,manager,kind,goal):
    if kind not in ('rules','links') or not isinstance(goal,str) or not 1<=len(goal.strip())<=2000:raise SafetyError('请输入明确的生成目标（1至2000字）')
    settings=manager.settings()
    if not settings['enabled'] or not manager.vault.configured():raise SafetyError('请先在AI管理启用DeepSeek')
    targets=manager.targets()[:120]
    for target in targets:
        st=safe_path(app.root,target['path']).stat();target['identity']=[st.st_dev,st.st_ino]
    return {'id':uid(),'kind':kind,'at':now(),'policy_hash':app.policy.hash,'settings':settings,'targets':targets,
            'request':{'goal':goal.strip(),'categories':app.policy.data['categories'],'candidate_targets':[{'id':t['id'],'path':t['path']} for t in targets]}}


def run(app,manager,packet):
    with instance_lock(app.state.profile):
        if packet['policy_hash']!=Policy(app.root).hash or packet['settings']!=manager.settings():raise SafetyError('规则或AI设置变化，请重新预览')
        if manager.status()['calls_today']>=manager.settings()['daily_limit']:raise SafetyError('达到每日AI调用上限')
        provider=manager.provider_factory(manager.vault.read(),model=manager.settings()['model']);history=app.state.get('ai_history',[])
        record={'id':packet['id'],'at':now(),'model':manager.settings()['model'],'items':1,'kind':packet['kind'],'status':'failed','usage':{}}
        try:
            answer=provider.design(packet['kind'],packet['request']);targets={t['id']:t for t in packet['targets']}
            def resolve(ident):
                target=targets.get(ident)
                if not target:raise SafetyError('AI返回了未知目录')
                p=safe_path(app.root,target['path']);st=p.stat()
                if [st.st_dev,st.st_ino]!=target['identity']:raise SafetyError('候选目录身份变化，旧建议不采用')
                return target
            if packet['kind']=='rules':
                rows=answer.get('rules')
                if not isinstance(rows,list) or not 1<=len(rows)<=5:raise SafetyError('AI规则数量无效')
                result=[]
                for row in rows:
                    target=resolve(row.get('target_id'));rule={'id':'ai_'+uid()[:12],'all':row.get('all'),'level':row.get('level',100),'target_parent':target['path']}
                    Policy.from_data(app.root,{**app.policy.data,'executable_rules':[rule]})
                    result.append({'rule':rule,'enabled':False,'title':clean(row.get('title','AI建议'),120),'note':clean(row.get('reason',''),1000)})
                output={'rules':result}
            else:
                rows=answer.get('links')
                if not isinstance(rows,list) or len(rows)>10:raise SafetyError('AI关联数量无效')
                result=[];seen=set()
                for row in rows:
                    a=resolve(row.get('source_id'));b=resolve(row.get('target_id'))
                    pair=tuple(sorted((a['path'],b['path'])))
                    if a['path']==b['path'] or a['path'].startswith(b['path']+'/') or b['path'].startswith(a['path']+'/') or pair in seen:continue
                    seen.add(pair);result.append({'source':a,'target':b,'reason':clean(row.get('reason',''),300)})
                output={'links':result}
            if Policy(app.root).hash!=packet['policy_hash']:raise SafetyError('AI返回期间规则变化，未采用建议')
            record['status']='completed';return {**output,'files_moved':False,'rules_published':False,'usage':provider.usage}
        finally:
            record['usage']=getattr(provider,'usage',{});counts=app.state.get('ai_daily_counts',{});day=record['at'][:10]
            counts[day]=manager.status()['calls_today']+1;app.state.set('ai_daily_counts',{d:counts[d] for d in sorted(counts)[-32:]});app.state.set('ai_history',(history+[record])[-200:])
