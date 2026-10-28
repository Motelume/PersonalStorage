"""Conservative content-based file names and journaled, reversible renames."""
import json
import re
from pathlib import Path
from .common import SafetyError,safe_path,manifest,digest,uid,now,rename_no_replace,instance_lock
from .planner import in_scope
from .policy import Policy
from .extraction import extract


def ambiguous(name):
    return bool(re.fullmatch(r'(?:新建(?:文本文档|文档|文本)|无标题|未命名|下载|文档|document|file|untitled|scan|扫描件|扫描文档)(?:[\s_\-（(]*\d*[)）]?)?',Path(name).stem,re.I))


def check_name(source,name):
    if not isinstance(name,str) or name!=name.strip() or not name or name in ('.','..') or name.endswith(('.', ' ')):raise SafetyError('名称为空或包含非法结尾')
    if re.search(r'[<>:"/\\|?*\x00-\x1f]',name) or len(name.encode('utf-16-le'))>400:raise SafetyError('文件名包含非法字符或过长')
    if Path(name).suffix!=Path(source).suffix:raise SafetyError('必须保留原扩展名和大小写')
    if re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])',name.split('.')[0].rstrip(),re.I):raise SafetyError('Windows保留名称不可使用')
    return name


def source_path(app,source):
    if not isinstance(source,str) or not in_scope(source,True,True,True):raise SafetyError('请选择资料库内普通资料，管理目录不改名')
    p=safe_path(app.root,source)
    if not p.is_file():raise SafetyError('只支持独立文件，不改变目录或资料包内部结构')
    supported={'.txt','.md','.csv','.tsv','.pdf','.doc','.docx','.ppt','.pptx','.xls','.xlsx','.rtf','.html','.htm','.odt','.ods','.odp','.epub','.eml','.srt','.vtt','.png','.jpg','.jpeg','.bmp','.webp','.tif','.tiff','.wav','.mp3','.m4a','.mp4','.mov'}
    if p.suffix.lower() not in supported or any(part.casefold() in ('.git','.venv','node_modules','__pycache__') for part in p.parts):raise SafetyError('本功能仅处理文档、图片和音视频资料，不改变程序或依赖文件')
    # Indexed directory units are indivisible; their members are not independent rename inputs.
    for row in app.state.db.execute("SELECT path FROM items WHERE kind='directory'"):
        if source.casefold().startswith(row['path'].casefold()+'/'):raise SafetyError('文件属于已登记的完整资料包，请保持包内结构')
    for row in app.state.db.execute("SELECT data FROM operations WHERE status='source_retained'"):
        if json.loads(row[0]).get('target')==source:raise SafetyError('此副本仍有待清理原件，请先核对外部转移记录')
    return p


def suggest(app,source):
    p=source_path(app,source);before=manifest(p)
    result={'source':source,'original':p.name,'suggested':p.name,'eligible':ambiguous(p.name),'reason':'现有名称具有明确含义，保留原名','manifest':before,'policy_hash':app.policy.hash}
    if result['eligible']:
        features=extract(p,limit=4000)
        if manifest(p)!=before:raise SafetyError('读取期间文件变化')
        result['reason']='没有找到可靠的标题，保留原名，可补充说明后请求AI建议'
        lines=features.get('text','').splitlines()[:12]
        candidates=[]
        for index,line in enumerate(lines):
            title=re.sub(r'^\s*(?:#{1,6}\s*|标题[：:]\s*)','',line).strip()
            if title.startswith('关于') and not title.endswith(('通知','方案','公告')) and index+1<len(lines):
                continuation=lines[index+1].strip()
                if continuation.endswith(('通知','方案','公告')) and len(title+continuation)<=64:title+=continuation
            if not 4<=len(title)<=64 or len(re.findall(r'[\u4e00-\u9fffA-Za-z]',title))<4:continue
            if re.search(r'\b(?:sk-|password|token|secret|api.?key)\b|身份证|账号|密码|学号|姓名|手机|\d{11,}|@|https?://',title,re.I):continue
            if re.fullmatch(r'(?:part|chapter|section|module)\s+[\w\d .-]+',title,re.I) and len(title.split())<=3:continue
            if re.fullmatch(r'(?:laboratory\s+(?:manual|[A-Z])|[A-Z]?\d{4,}\s+laboratory\s+[A-Z])',title,re.I):continue
            if re.search(r'UNIVERSITY|COLLEGE|大学|学院|培训中心',title,re.I) and not re.search(r'通知|关于|报告|课程|实验|研究|设计',title):continue
            try:name=check_name(source,title+p.suffix)
            except SafetyError:continue
            if ambiguous(name):continue
            if name not in candidates:candidates.append(name)
        if candidates:result.update(suggested=candidates[0],candidates=candidates[:3],reason='依据正文标题和相邻标题行生成；可选择候选并核对用途')
    return result


def preview(app,source,name,reason):
    p=source_path(app,source);check_name(source,name)
    if not ambiguous(p.name):raise SafetyError('原名称已有明确含义，本功能保持原名')
    if name==p.name:raise SafetyError('名称未改变，无需执行')
    if not isinstance(reason,str) or not reason.strip():raise SafetyError('请保留或填写改名依据')
    target=p.with_name(name).relative_to(app.root).as_posix()
    if len(str(app.root/target))>240 or safe_path(app.root,target).exists():raise SafetyError('目标已存在或路径过长，不能覆盖')
    return {'id':uid(),'source':source,'target':target,'manifest':manifest(p),'policy_hash':app.policy.hash,'reason':reason[:1000]}


def renamed_manifest(plan):
    m=json.loads(json.dumps(plan['manifest']));m['files'][0]['path']=Path(plan['target']).name;return m


def finish(app,plan):
    source=plan['source'];target=plan['target'];expected=renamed_manifest(plan)
    if safe_path(app.root,source).exists() or manifest(safe_path(app.root,target))!=expected:raise SafetyError('改名结果不唯一或内容变化，请核对恢复记录')
    search=app.state.profile/'search.sqlite3'
    if search.exists():
        from .searching import database
        db=database(app.state)
        try:
            with db:
                db.execute('DELETE FROM documents WHERE path IN (?,?)',(source,target));db.execute('DELETE FROM document_fts WHERE path IN (?,?)',(source,target))
        finally:db.close()
    with app.state.db:
        item=app.state.item(source)
        if item:
            app.state.db.execute('UPDATE items SET path=?,data=?,seen_at=? WHERE id=?',(target,json.dumps(expected),now(),item['id']))
            app.state.db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?)',(uid(),item['id'],digest(expected),json.dumps(expected),now()))
            # The file-name-bearing fingerprint changed; old review bindings must not be reused.
            app.state.db.execute("UPDATE reviews SET status='superseded' WHERE item_id=? AND status NOT IN ('confirmed','resolved_external')",(item['id'],))
            a=app.state.db.execute('SELECT data FROM assignments WHERE item_id=?',(item['id'],)).fetchone()
            app.state.add_review(item['id'],digest(expected),json.loads(a[0]) if a else {},'content_or_assignment_changed','waiting_user')
        app.state.db.execute('DELETE FROM ai_location_cache WHERE path IN (?,?)',(source,target))
        from .scanning import metadata
        for row in app.state.db.execute('SELECT source,data FROM inbox_imports').fetchall():
            value=json.loads(row['data'])
            if value.get('target')==source:
                value.update(target=target,target_metadata=metadata(safe_path(app.root,target))['fingerprint'],renamed=True)
                app.state.db.execute('UPDATE inbox_imports SET data=? WHERE source=?',(json.dumps(value),row['source']))
        for row in app.state.db.execute('SELECT id,data FROM graph_links').fetchall():
            value=json.loads(row['data'])
            for field in ('source','target'):
                if isinstance(value.get(field),dict) and value[field].get('path')==source:value[field]['path']=target
            app.state.db.execute('UPDATE graph_links SET data=? WHERE id=?',(json.dumps(value),row['id']))
        for key in ('classification_notes','held_sources'):
            row=app.state.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            if row:
                value=json.loads(row[0])
                if source in value:value[target]=value.pop(source)
                app.state.db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(value),key))
        prefs=app.state.get('overview_preferences')
        if prefs:
            for field in ('focus','selected'):
                if prefs.get(field)==source:prefs[field]=target
            prefs['expanded']=[target if p==source else p for p in prefs.get('expanded',[])]
            prefs['positions']={target if p==source else p:v for p,v in prefs.get('positions',{}).items()}
            app.state.db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(prefs),'overview_preferences'))
        app.state.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('file_rename_journal','null'))
        history=app.state.get('file_rename_history',[])
        if not any(x['id']==plan['id'] for x in history):history.append({**plan,'completed_at':now()})
        app.state.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('file_rename_history',json.dumps(history)))
        app.state.db.execute('DELETE FROM plans')
    return {'source':source,'target':target,'operation_id':plan['id'],'contents_preserved':True}


def apply(app,plan):
    with instance_lock(app.state.profile):
        if app.state.get('paused',False):raise SafetyError('执行已暂停')
        if app.state.get('file_rename_journal') or app.state.db.execute("SELECT COUNT(*) FROM operations WHERE status IN ('prepared','moved','verified','recovery_required')").fetchone()[0]:raise SafetyError('请先核对未完成操作')
        if Policy(app.root).hash!=plan['policy_hash'] or manifest(source_path(app,plan['source']))!=plan['manifest']:raise SafetyError('文件或规则变化，请重新预览')
        if app.state.item(plan['target']):raise SafetyError('目标有历史索引记录，请先核对')
        app.state.set('file_rename_journal',plan)
        try:rename_no_replace(safe_path(app.root,plan['source']),safe_path(app.root,plan['target']))
        except Exception:
            if safe_path(app.root,plan['source']).exists() and not safe_path(app.root,plan['target']).exists():app.state.set('file_rename_journal',None)
            raise
        return finish(app,plan)


def recover(app):
    plan=app.state.get('file_rename_journal')
    if not plan:return
    if safe_path(app.root,plan['source']).exists() and not safe_path(app.root,plan['target']).exists() and manifest(safe_path(app.root,plan['source']))==plan['manifest']:
        app.state.set('file_rename_journal',None);return
    finish(app,plan)


def undo_preview(app,ident):
    plan=next((x for x in app.state.get('file_rename_history',[]) if x['id']==ident),None)
    if not plan:raise SafetyError('改名记录不存在')
    if safe_path(app.root,plan['source']).exists() or manifest(source_path(app,plan['target']))!=renamed_manifest(plan):raise SafetyError('文件已变化或原名被占用，不能撤销')
    return {'id':uid(),'source':plan['target'],'target':plan['source'],'manifest':renamed_manifest(plan),'policy_hash':app.policy.hash,'reason':'用户确认撤销改名','undo_of':ident}


def ai_prepare(app,manager,source,note):
    p=source_path(app,source)
    if not ambiguous(p.name):raise SafetyError('现有名称明确，不向AI发送改名任务')
    settings=manager.settings()
    if not settings['enabled'] or not manager.vault.configured():raise SafetyError('请先启用AI')
    if not isinstance(note,str) or len(note)>2000:raise SafetyError('补充说明最多2000字')
    before=manifest(p);features=extract(p,limit=4000) if settings['with_content'] else {}
    if manifest(p)!=before:raise SafetyError('文件读取期间变化')
    return {'id':uid(),'kind':'rename','at':now(),'policy_hash':app.policy.hash,'settings':settings,'targets':[],
            'source':source,'manifest':before,'request':{'name':p.name,'text':features.get('text',''),'user_context':note}}


def validate_answer(app,packet,answer):
    p=source_path(app,packet['source'])
    if manifest(p)!=packet['manifest']:raise SafetyError('AI判断期间原件变化，旧建议不采用')
    if answer.get('keep') is not False:return {'source':packet['source'],'suggested':p.name,'reason':'AI建议保留原名，或未提供明确改名依据'}
    name=check_name(packet['source'],answer.get('name'));reason=answer.get('reason')
    if not isinstance(reason,str) or not reason.strip():raise SafetyError('AI未提供改名依据')
    return {'source':packet['source'],'suggested':name,'reason':reason[:1000]}
