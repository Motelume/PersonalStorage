"""Rule-first inbox classification with a bounded AI fallback."""
import json
from pathlib import Path
from .common import SafetyError,manifest,digest,now
from . import inboxes,ai_audit

def human_task(app,path,m,reason):
    item=app.state.observe(path,m['kind'],m);assignment=app.state.db.execute('SELECT data FROM assignments WHERE item_id=?',(item,)).fetchone()
    current=json.loads(assignment[0]) if assignment else {}
    with app.state.db:app.state.add_review(item,digest(m),current,reason,'waiting_user')

def promote(app,obj):
    row=app.state.db.execute("SELECT r.*,i.path FROM reviews r JOIN items i ON i.id=r.item_id WHERE i.path=? AND r.status='correction_required' ORDER BY r.rowid DESC LIMIT 1",(obj['path'],)).fetchone()
    if not row:return False
    data=json.loads(row['data']);result=data.get('result',{})
    if result.get('decision')!='suggest_reclassify':return False
    path=inboxes.resolve(app.root,app.state,obj['path']);m=manifest(path)
    if digest(m)!=data['version']:return False
    target=app.policy.target(result['target_parent'],path.name)
    obj['fingerprint']=data['version'];obj['review_id']=row['id']
    from .review import review_context
    obj['review_context_hash']=review_context(row)
    obj['action']={**obj.get('action',{}),'source':obj['path'],'decision':'move','target':target,'target_parent':result['target_parent'],
        'rule_id':'ai_classification','evidence':result['reason'],'ai_classified':True,'classification_verified':True,'manifest_hash':data['version']}
    if __import__('pathlib').Path(app.root/target).exists():obj['action'].update(decision='conflict',reason='目标同名，不能覆盖')
    return obj['action']['decision']=='move'

def fallback(app,manager,rows,objects,job):
    config=manager.settings();report={'rule_classified':sum(r['decision']=='move' for r in rows),'ai_classified':0,'needs_input':0,'batches':0,'cache_hits':0,'unprocessed':0,'tokens':0}
    unknown=[(row,obj) for row,obj in zip(rows,objects) if row['decision']=='review']
    for row,obj in zip(rows,objects):
        if row['decision']=='move':row['classification_verified']=True;obj['action']['classification_verified']=True
    if not config['enabled'] or not manager.vault.configured():report['unprocessed']=len(unknown);report['notice']='AI未启用，未确定资料保留待确认';return report
    pending=[]
    def apply(batch):
        if not batch:return
        if manager.status()['calls_today']>=config['daily_limit']:
            report['unprocessed']+=len(batch);report['notice']='达到每日AI上限，其余资料保留待下次处理';return
        job.update(phase='规则分类已完成，AI正在阅读未确定资料')
        packet=manager.prepare([obj for _,obj in batch]);result=manager.run(packet)
        report['batches']+=1;report['tokens']+=result['usage'].get('total_tokens',0)
        for row,obj in batch:
            if promote(app,obj):row.update(obj['action']);report['ai_classified']+=1
            else:report['needs_input']+=1
    for index,(row,obj) in enumerate(unknown):
        try:
            m=manifest(inboxes.resolve(app.root,app.state,obj['path']));old=ai_audit.cached(app,obj['path'],m,config['model'])
            if old and old['decision']=='suggest_reclassify' and promote(app,obj):row.update(obj['action']);report['cache_hits']+=1;report['ai_classified']+=1
            elif old and old['decision']=='needs_user_input':human_task(app,obj['path'],m,old['reason']);report['cache_hits']+=1;report['needs_input']+=1
            else:pending.append((row,obj))
            if len(pending)>=config['max_items']:apply(pending);pending=[]
        except (OSError,SafetyError) as exc:
            report['notice']=str(exc);report['unprocessed']=len(unknown)-index
            for waiting,wobj in pending:
                try:human_task(app,wobj['path'],manifest(inboxes.resolve(app.root,app.state,wobj['path'])),'AI本次不可用，需人工确认')
                except (OSError,SafetyError):pass
            pending=[];break
    if pending:
        try:apply(pending)
        except (OSError,SafetyError) as exc:report['notice']=str(exc);report['unprocessed']+=len(pending)
    return report
