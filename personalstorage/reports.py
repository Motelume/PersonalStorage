from pathlib import Path
from collections import Counter
import json
from .common import now

def status(state):
    def grouped(table,field): return dict(state.db.execute(f'SELECT {field},COUNT(*) FROM {table} GROUP BY {field}').fetchall())
    return {'root':str(state.root),'profile':str(state.profile),'at':now(),'paused':state.get('paused',False),'indexed_items':state.db.execute('SELECT COUNT(*) FROM items').fetchone()[0],'operations':grouped('operations','status'),'review_queue':grouped('reviews','status'),'background_authorized':bool(state.get('automation')),'log_export_warning':state.get('log_export_warning')}

def search(state,query,limit=100):
    rows=state.db.execute("SELECT i.*,a.version AS assignment_version,a.data AS assignment FROM items i LEFT JOIN assignments a ON a.item_id=i.id WHERE i.path LIKE ? ESCAPE '\\' ORDER BY i.path LIMIT ?",('%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%',limit)).fetchall()
    return [{'item_id':r['id'],'path':r['path'],'kind':r['kind'],'last_seen_at':r['seen_at'],'integrity':json.loads(r['data']).get('integrity','sha256_at_recorded_time'),'assignment':json.loads(r['assignment']) if r['assignment'] else None} for r in rows]

def pending(state):
    rows=state.db.execute("SELECT r.id,r.status,r.data,i.path FROM reviews r JOIN items i ON i.id=r.item_id WHERE r.status NOT IN ('confirmed','superseded','resolved_external') ORDER BY r.rowid").fetchall()
    return [{'review_id':r['id'],'status':r['status'],'path':r['path'],'details':json.loads(r['data'])} for r in rows]


def resolved_reviews(state):
    rows = state.db.execute("SELECT id,data FROM reviews WHERE status IN ('resolved_external','superseded') ORDER BY rowid DESC").fetchall()
    out = []
    for row in rows:
        data = json.loads(row['data']); resolution = data.get('resolution', {})
        if resolution.get('kind') == 'external_handled':
            out.append({'review_id': row['id'], 'status': 'resolved_external',
                        'path': resolution['source'], 'details': data})
    return out

def operations(state,limit=100):
    return [{'id':r['id'],'status':r['status'],'at':r['updated_at'],'data':json.loads(r['data'])} for r in state.db.execute('SELECT * FROM operations ORDER BY rowid DESC LIMIT ?',(limit,))]

def markdown(state):
    s=status(state)
    lines=['# PersonalStorage 本机状态报告','',f'生成时间：{s["at"]}',f'资料库：`{s["root"]}`',f'已索引条目：{s["indexed_items"]}；暂停：{s["paused"]}。','', '## 操作状态','']
    lines += ['- '+k+'：'+str(v) for k,v in s['operations'].items()]
    lines += ['','## 待办','', '| 位置 | 状态 | 原因 |','|---|---|---|']
    for r in pending(state):
        lines.append('| '+r['path'].replace('|','\\|')+' | '+r['status']+' | '+r['details']['reason'].replace('|','\\|')+' |')
    lines += ['','未变内容不重复审计；历史基线未被自动标记为已审核。索引记录是历史观测，不等于此刻文件仍存在。','']
    return '\n'.join(lines)

def offline_search(root,query):
    folder=Path(root)/'09_移动硬盘管理/硬盘内容索引'; out=[]
    for p in folder.rglob('*.json') if folder.exists() else []:
        try:
            d=json.loads(p.read_text(encoding='utf-8-sig'))
            for e in d.get('entries',[]):
                if query.casefold() in e.get('relative_path','').casefold(): out.append({'index':str(p),'disk_identity':d.get('disk_identity'),'scanned_at':d.get('scanned_at'),'entry':e,'realtime_verified':False})
        except (OSError,ValueError,TypeError): continue
    return out
