"""Versioned shared rules with additive, explicitly approved local updates."""
import json
import hashlib
from pathlib import Path
import shutil
from .common import SafetyError, read_json, write_json, digest, sha, uid, now, instance_lock, safe_path

TEMPLATES=Path(__file__).with_name('templates')
MANAGED_FIELDS=('subcategory_rules','classification_rules','evidence_rules','task_status','external_storage','path_policy','initialization','interpretation','network_policy')


def template():
    return read_json(TEMPLATES/'classification-config.json')


def document_hash(path):
    return hashlib.sha256(Path(path).read_text(encoding='utf-8-sig').encode('utf-8')).hexdigest()


def marker(data=None):
    data=data or template()
    return {'package':data['shared_package'],'template_hash':digest(data),
        'document_sha256':document_hash(TEMPLATES/'classification.md'),
        'field_hashes':{k:digest(data[k]) for k in MANAGED_FIELDS if k in data},'at':now()}


def preview(app):
    data=template();directory=safe_path(app.root,'_整理规则');document=safe_path(app.root,'_整理规则/通用分类规则.md');meta=safe_path(app.root,'_整理规则/通用规则版本.json')
    previous=read_json(meta) if meta.is_file() else None
    if document.is_file():
        current_hash=document_hash(document)
        if current_hash==marker(data)['document_sha256']:document_action='unchanged'
        elif previous and previous.get('package',{}).get('id')==data['shared_package']['id'] and current_hash==previous.get('document_sha256'):document_action='update'
        else:document_action='preserve_local_edits'
    elif document.exists():raise SafetyError('通用规则说明位置被目录占用')
    else:document_action='create'
    updates=[k for k in MANAGED_FIELDS if k in app.policy.data and previous
        and previous.get('field_hashes',{}).get(k)==digest(app.policy.data[k]) and digest(data[k])!=digest(app.policy.data[k])]
    return {'package':data['shared_package'],'installed_package':previous.get('package') if previous else None,
        'schema_compatible':app.policy.data['schema_version']==data['shared_package']['compatible_schema'],
        'add_fields':[k for k in MANAGED_FIELDS if k not in app.policy.data],'update_unmodified_fields':updates,
        'preserve_existing_fields':[k for k in MANAGED_FIELDS if k in app.policy.data and k not in updates],
        'document_action':document_action,'policy_hash':app.policy.hash,'template_hash':digest(data),
        'private_tasks_preserved':True,'executable_rules_preserved':True,'original_documents_touched':False}


def update(app):
    from .policy import Policy
    with instance_lock(app.state.profile):
        unfinished=app.state.db.execute("SELECT COUNT(*) FROM operations WHERE status IN ('prepared','moved','verified','recovery_required')").fetchone()[0]
        if unfinished:raise SafetyError('有未完成的文件操作，请先恢复记录')
        plan=preview(app)
        if not plan['schema_compatible']:raise SafetyError('规则包与当前配置schema不兼容，不能自动更新')
        if Policy(app.root).hash!=plan['policy_hash']:raise SafetyError('当前规则已变化，请重新预览')
        if not plan['add_fields'] and not plan['update_unmodified_fields'] and plan['document_action']=='unchanged' and plan['installed_package']==plan['package']:
            return {**plan,'updated':False,'reason':'已是当前通用规则版本'}
        directory=app.root/'_整理规则';config=directory/'分类配置.json';document=directory/'通用分类规则.md';meta=directory/'通用规则版本.json'
        backup=app.state.profile/'rule-backups'/uid();backup.mkdir(parents=True)
        app.state.snapshot(backup/'state.sqlite3')
        for path in (config,document,meta):
            if path.is_file():shutil.copy2(path,backup/path.name)
        original=config.read_bytes();data=json.loads(original.decode('utf-8-sig'));base=template()
        if digest(base)!=plan['template_hash']:raise SafetyError('规则包在预览后变化，请重新检查')
        changed_fields=plan['add_fields']+plan['update_unmodified_fields']
        for key in changed_fields:data[key]=base[key]
        try:
            if changed_fields:write_json(config,data)
            if plan['document_action'] in ('create','update'):
                document.write_text((TEMPLATES/'classification.md').read_text(encoding='utf-8'),encoding='utf-8')
            if plan['document_action']!='preserve_local_edits':write_json(meta,marker(base))
            Policy(app.root)
            if changed_fields:app.state.set('automation',None)
        except BaseException:
            config.write_bytes(original)
            for path in (document,meta):
                saved=backup/path.name
                if saved.is_file():shutil.copy2(saved,path)
            raise
        return {**plan,'updated':True,'backup':str(backup),'background_authorization_revoked':bool(changed_fields)}
