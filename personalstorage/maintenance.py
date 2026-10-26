"""Read-only filesystem reconciliation; it never reorders or deletes original files."""
import json
from pathlib import Path
from .common import safe_path,manifest,digest,now,uid,SafetyError

def reconcile(root,state,policy,full_hash=False):
    rows=state.db.execute('SELECT i.*,a.data AS assignment FROM items i LEFT JOIN assignments a ON a.item_id=i.id').fetchall()
    out=[]
    for row in rows:
        data=json.loads(row['data']); assignment=json.loads(row['assignment']) if row['assignment'] else {}
        # Active registered units already cover their members; history stays metadata-only unless requested.
        if not assignment and not full_hash: continue
        try:
            p=safe_path(root,row['path'])
            if not p.exists():
                with state.db: state.db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?)',(row['id'],'missing_observed',now(),0))
                out.append({'path':row['path'],'status':'missing_observed','moved':False}); continue
            current=manifest(p); changed=current!=data
            policy_changed=bool(assignment and assignment.get('rule_fingerprint')!=policy.decision_fingerprint(assignment['rule_id']))
            with state.db:
                if policy_changed:
                    assignment={**assignment,'rule_fingerprint':policy.decision_fingerprint(assignment['rule_id']),'policy_hash':policy.hash}
                    state.db.execute('UPDATE assignments SET version=version+1,data=? WHERE item_id=?',(json.dumps(assignment,ensure_ascii=False),row['id']))
                state.db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?)',(row['id'],'online',now(),1))
                if changed:
                    state.db.execute('UPDATE items SET data=?,seen_at=? WHERE id=?',(json.dumps(current,ensure_ascii=False),now(),row['id']))
                    state.db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?)',(uid(),row['id'],digest(current),json.dumps(current,ensure_ascii=False),now()))
                if assignment and (changed or policy_changed):
                    state.db.execute("UPDATE reviews SET status='superseded' WHERE item_id=?",(row['id'],))
                    state.add_review(row['id'],digest(current),assignment,'content_changed' if changed else 'relevant_rule_changed')
            out.append({'path':row['path'],'status':'changed' if changed else 'rule_changed' if policy_changed else 'unchanged','moved':False})
        except (OSError,SafetyError) as e:
            with state.db: state.db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?)',(row['id'],'unavailable',now(),0))
            out.append({'path':row['path'],'status':'unavailable','error':str(e),'moved':False})
    return out
