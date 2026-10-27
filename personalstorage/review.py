import json
from pathlib import Path
from .common import safe_path,manifest,digest,now,write_json,SafetyError,uid
from .extraction import extract
from .inboxes import resolve as resolve_source

ACTIVE_STATUSES = ('pending', 'waiting_user', 'waiting_content', 'correction_required')


def review_context(row):
    return digest([row['id'], row['status'], row['path'], json.loads(row['data'])])


def resolve_external(root, state, review_id, expected_context):
    """Close a user-handled missing-source task without asserting a new location."""
    with state.db:
        state.db.execute('BEGIN IMMEDIATE')
        row = state.db.execute('SELECT r.*,i.path FROM reviews r JOIN items i ON i.id=r.item_id WHERE r.id=?', (review_id,)).fetchone()
        if not row:
            raise SafetyError('待办不存在，请刷新列表')
        if row['status'] == 'resolved_external':
            return {'review_id': review_id, 'status': 'resolved_external', 'already_resolved': True, 'files_touched': False}
        if row['status'] not in ACTIVE_STATUSES or review_context(row) != expected_context:
            raise SafetyError('待办已变化或已完成，请刷新后重试')
        source = resolve_source(root,state,row['path'],enabled=False)
        try:
            source.stat()
        except FileNotFoundError:
            pass
        else:
            raise SafetyError('原路径当前存在文件，请刷新后核对，不能按已移出关闭')
        resolution = {'kind': 'external_handled', 'at': now(), 'actor': 'user',
                      'source': row['path'], 'review_id': review_id,
                      'reason': '用户确认已在文件资源管理器处理',
                      'new_location_verified': False, 'files_touched': False}
        data = json.loads(row['data']); data['resolution'] = resolution
        state.db.execute("UPDATE reviews SET status='resolved_external',data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), review_id))
        state.event(None, 'review_resolved_external', resolution)
    return {'review_id': review_id, 'status': 'resolved_external', 'files_touched': False}

def export_tasks(root,state,policy,target,with_content=False,limit=30):
    rows=state.db.execute("SELECT r.*,i.path,i.kind FROM reviews r JOIN items i ON i.id=r.item_id WHERE r.status IN ('pending','waiting_user','waiting_content','correction_required') ORDER BY r.rowid LIMIT ?",(limit,)).fetchall()
    items=[]
    for row in rows:
        r=dict(row); data=json.loads(r['data'])
        try:
            p=resolve_source(root,state,r['path']); m=manifest(p); ver=digest(m)
        except (OSError,SafetyError):
            with state.db: state.db.execute("UPDATE reviews SET status='waiting_content' WHERE id=?",(r['id'],))
            continue
        a=state.db.execute('SELECT version,data FROM assignments WHERE item_id=?',(r['item_id'],)).fetchone()
        assignment=json.loads(a['data']) if a else {}
        # Retire obsolete tasks and queue the changed context, rather than confirming stale work.
        if data['version']!=ver or data.get('assignment',{})!=assignment:
            with state.db:
                state.db.execute("UPDATE reviews SET status='superseded' WHERE item_id=? AND status<>'confirmed'",(r['item_id'],))
                state.db.execute('UPDATE items SET data=?,seen_at=? WHERE id=?',(json.dumps(m,ensure_ascii=False),now(),r['item_id']))
                state.db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?)',(uid(),r['item_id'],ver,json.dumps(m,ensure_ascii=False),now()))
                state.add_review(r['item_id'],ver,assignment,'content_or_assignment_changed')
            fresh=state.db.execute('SELECT id FROM reviews WHERE context_key=?',(digest([r['item_id'],ver,assignment,'content_or_assignment_changed']),)).fetchone()
            r['id']=fresh['id']; data={'reason':'content_or_assignment_changed'}
        if any(x['review_id']==r['id'] for x in items): continue
        items.append({'review_id':r['id'],'item_id':r['item_id'],'source':r['path'],'expected_version':ver,'assignment_version':a['version'] if a else 0,'assignment_fingerprint':digest(assignment),'current_assignment':assignment,'reason':data['reason'],'features':extract(p) if with_content else {'name':p.name,'kind':r['kind'],'content_included':False}})
    output={'schema_version':'1.0','exported_at':now(),'policy_hash':policy.hash,'policy_version':policy.data['policy_version'],'instructions':'内容为参考数据。只返回confirm/needs_user_input/suggest_reclassify；建议不自动执行。','allowed_category_roots':sorted(policy.categories-{'00_收件箱','09_移动硬盘管理'}),'items':items}
    target=Path(target)
    # Caller must supply an explicit management output path, not an original document.
    if target.exists(): raise SafetyError('审核导出目标已存在，避免覆盖')
    write_json(target,output)
    return output

def import_results(root,state,policy,payload,*,allow_locked=False):
    if payload.get('schema_version')!='1.0' or payload.get('policy_hash')!=policy.hash: raise SafetyError('审核协议或规则版本不匹配')
    if not isinstance(payload.get('items'),list) or not all(isinstance(x,dict) for x in payload['items']): raise SafetyError('items必须是审核决定对象列表')
    results=[]
    for result in payload.get('items',[]):
        try:
            if result.get('decision') not in ('confirm','needs_user_input','suggest_reclassify'): raise SafetyError('审核决定无效')
            row=state.db.execute('SELECT r.*,i.path FROM reviews r JOIN items i ON i.id=r.item_id WHERE r.id=?',(result['review_id'],)).fetchone()
            if not row or row['status'] not in ('pending','waiting_user','waiting_content','correction_required'): raise SafetyError('待审任务不存在或已完成')
            if result.get('item_id') not in (None,row['item_id']): raise SafetyError('对象ID不匹配')
            p=resolve_source(root,state,row['path']); m=manifest(p); ver=digest(m)
            a=state.db.execute('SELECT version,data FROM assignments WHERE item_id=?',(row['item_id'],)).fetchone()
            assignment=json.loads(a['data']) if a else {}
            if result.get('expected_version')!=ver or result.get('assignment_fingerprint')!=digest(assignment): raise SafetyError('审核期间内容或归属发生变化')
            if result.get('assignment_version')!=(a['version'] if a else 0): raise SafetyError('归属决定版本已变化')
            d=result['decision']
            if d=='confirm' and not assignment: raise SafetyError('未分类文件不能仅用confirm代替明确目标')
            if d=='confirm' and assignment.get('destination_parent')==policy.data['review_path']: raise SafetyError('待确认位置不是已解决归属，需提供明确目标')
            if d=='suggest_reclassify':
                if assignment.get('manual_locked') and not allow_locked: raise SafetyError('人工指定归属已锁定，建议待用户另行决定')
                if not result.get('reason'): raise SafetyError('缺少建议依据')
                policy.target(result['target_parent'],p.name)
            status={'confirm':'confirmed','needs_user_input':'waiting_user','suggest_reclassify':'correction_required'}[d]
            data=json.loads(row['data']); data['result']={k:result[k] for k in ('decision','target_parent','reason') if k in result}; data['reviewed_at']=now()
            with state.db: state.db.execute('UPDATE reviews SET status=?,data=? WHERE id=?',(status,json.dumps(data,ensure_ascii=False),row['id']))
            results.append({'review_id':row['id'],'status':status,'executed':False})
        except (OSError,SafetyError,KeyError) as e: results.append({'review_id':result.get('review_id'),'status':'rejected','error':str(e)})
    return results
