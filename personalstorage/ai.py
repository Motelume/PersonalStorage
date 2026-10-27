"""User-triggered, version-bound review orchestration. Never moves files."""
import json
import re
from pathlib import Path
from .common import SafetyError, now, uid, digest, safe_path, manifest, linked, instance_lock
from .credentials import CredentialVault
from .model_client import DeepSeekProvider, MODELS
from .extraction import extract
from .review import ACTIVE_STATUSES, import_results
from .planner import in_scope
from .inboxes import resolve as resolve_source, PREFIX as EXTERNAL_PREFIX
from . import ai_audit

DEFAULTS = {'enabled':False,'model':'deepseek-flash','with_content':True,'max_items':5,'daily_limit':30}


class AIManager:
    def __init__(self, app, vault=None, provider_factory=None):
        self.app=app; self.vault=vault or CredentialVault(); self.provider_factory=provider_factory or DeepSeekProvider

    def settings(self):
        return {**DEFAULTS, **self.app.state.get('ai_settings',{})}

    def status(self):
        history=self.app.state.get('ai_history',[])
        today=now()[:10]
        return {'settings':self.settings(),'configured':self.vault.configured(),'models':list(MODELS),
            'provider':'DeepSeek','endpoint':'https://api.deepseek.com','credential_storage':'Windows DPAPI（当前用户）',
            'calls_today':self.app.state.get('ai_daily_counts',{}).get(today,sum(r['at'][:10]==today for r in history)),
            'tokens_recorded':sum(r.get('usage',{}).get('total_tokens',0) for r in history),
            'connection':self.app.state.get('ai_connection'), 'history':list(reversed(history[-30:]))}

    def configure(self, body):
        config=self.settings()
        for key in ('enabled','with_content'):
            if key in body:
                if not isinstance(body[key],bool): raise SafetyError('AI开关参数无效')
                config[key]=body[key]
        if 'model' in body:
            if body['model'] not in MODELS: raise SafetyError('请选择受支持的DeepSeek模型')
            config['model']=body['model']
        for key,maximum in (('max_items',10),('daily_limit',2000)):
            if key in body:
                value=body[key]
                if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=maximum: raise SafetyError('AI数量限制无效')
                config[key]=value
        if 'api_key' in body and not isinstance(body['api_key'],str):raise SafetyError('密钥须为文本')
        if body.get('api_key'):
            self.vault.save(body['api_key']);self.app.state.set('ai_connection',None)
        elif config['model']!=self.settings()['model']:self.app.state.set('ai_connection',None)
        self.app.state.set('ai_settings',config)
        return self.status()

    def test(self):
        result=self.provider_factory(self.vault.read(),model=self.settings()['model']).test()
        self.app.state.set('ai_connection',{'at':now(),**result})
        return result

    def clear_key(self):
        self.vault.clear(); self.app.state.set('ai_settings',{**self.settings(),'enabled':False})
        self.app.state.set('ai_connection',None)
        return {'cleared':True}

    def targets(self,documents=None):
        from collections import deque
        result=[]; root=self.app.root
        stack=deque()
        for category in sorted(self.app.policy.categories-{'00_收件箱','09_移动硬盘管理','99_临时待整理'}):
            folder=safe_path(root,category)
            if folder.is_dir():stack.append(folder)
        while stack and len(result)<4096:
            directory=stack.popleft()
            if linked(directory): continue
            relative=directory.relative_to(root).as_posix()
            try: self.app.policy.target(relative,'__ai_check__')
            except SafetyError: continue
            result.append({'id':uid(),'path':relative})
            if len(directory.relative_to(root).parts)<8:
                try:
                    stack.extend(sorted([p for p in directory.iterdir() if p.is_dir() and not linked(p) and p.name not in {'.git','.venv','venv','node_modules','__pycache__'}]))
                except OSError: continue
        if not documents:return result[:400]
        search=' '.join(str(d.get(k,'')) for d in documents for k in ('name','source','text','user_context'))[:16000].casefold()
        terms=set(re.findall(r'[a-z0-9]{3,}',search))
        terms.update(search[i:i+2] for i in range(len(search)-1) if '\u4e00'<=search[i]<='\u9fff' and '\u4e00'<=search[i+1]<='\u9fff')
        parents={str(Path(d['source']).parent).replace('\\','/') for d in documents}
        def relevance(t):
            path=t['path'].casefold();return (10000 if t['path'] in parents else 0)+sum(1 for term in terms if term in path)
        return sorted(result,key=lambda t:(-relevance(t),t['path']))[:160]

    def prepare(self, objects, user_context=None, allow_manual_override=False):
        config=self.settings()
        if not config['enabled'] or not self.vault.configured(): raise SafetyError('请先在AI管理中保存密钥并启用AI审核')
        if not objects or len(objects)>config['max_items']: raise SafetyError('本批资料数量超过AI管理中的上限')
        if len({o['path'].casefold() for o in objects})!=len(objects): raise SafetyError('本批资料重复')
        documents=[]; bindings=[]
        user_context=user_context or {}
        if not isinstance(user_context,dict) or any(not isinstance(v,str) or len(v)>2000 for v in user_context.values()):raise SafetyError('每项补充说明最多2000字')
        if any(k not in {o['path'] for o in objects} for k in user_context):raise SafetyError('补充说明必须对应本次选定资料')
        if allow_manual_override and (len(objects)!=1 or not user_context.get(objects[0]['path'],'').strip()):raise SafetyError('重新判断人工归属须针对一项资料并提供补充说明')
        saved=self.app.state.get('classification_notes',{})
        supplied=dict(user_context)
        if user_context:
            saved.update(user_context)
        user_context=saved
        for obj in objects:
            if not (obj.get('action') or obj.get('review_id')): raise SafetyError('请选择收件箱或待办中的资料')
            external=obj['path'].startswith(EXTERNAL_PREFIX)
            storage_review=bool(obj.get('audit_mode') or obj.get('review_id')) and obj['path'].startswith('09_移动硬盘管理/待移入硬盘/')
            if not external and not storage_review and not in_scope(obj['path'],bool(obj.get('include_root') or obj.get('audit_mode')),True,bool(obj.get('review_id') or obj.get('audit_mode'))): raise SafetyError('资料不在本次审核范围')
            path=resolve_source(self.app.root,self.app.state,obj['path']); m=manifest(path)
            if not m['files']: raise SafetyError('空目录保持原位，不能提交AI审核')
            existing=self.app.state.item(obj['path'])
            assignment_row=self.app.state.db.execute('SELECT version,data FROM assignments WHERE item_id=?',(existing['id'],)).fetchone() if existing else None
            assignment=json.loads(assignment_row['data']) if assignment_row else {}
            if obj.get('review_id'):
                row=self.app.state.db.execute('SELECT status FROM reviews WHERE id=?',(obj['review_id'],)).fetchone()
                if not row or row['status'] not in ACTIVE_STATUSES: raise SafetyError('待办已变化，请刷新')
            feature=ai_audit.features(path,limit=4000) if config['with_content'] else {'name':path.name,'kind':m['kind'],'text':'','warnings':['未授权发送正文，仅使用名称和上下文']}
            ident=uid()
            documents.append({'id':ident,'name':path.name,'source':obj['path'],'current_assignment':assignment,
                'text':feature.get('text','')[:4000],'members':feature.get('members',[])[:40], 'warnings':feature.get('warnings',[]),
                'file_count':len(m['files']),'user_context':user_context.get(obj['path'],''),'audit_mode':bool(obj.get('audit_mode')),'user_requested_reclassification':bool(allow_manual_override)})
            allow_confirm_current=bool(obj.get('audit_mode') or obj.get('review_id')) and obj['path'].split('/')[0] in self.app.policy.categories and not obj['path'].startswith(('00_收件箱/',EXTERNAL_PREFIX))
            if allow_confirm_current and not assignment:
                documents[-1]['current_assignment']={'destination_parent':str(Path(obj['path']).parent).replace('\\','/'),'inferred_from_current_location':True}
            bindings.append({'id':ident,'path':obj['path'],'manifest':m,'version':digest(m),'item_id':existing['id'] if existing else None,
                'assignment':assignment,'assignment_version':assignment_row['version'] if assignment_row else 0,'audit_mode':bool(obj.get('audit_mode')),'storage_review':storage_review,'allow_confirm_current':allow_confirm_current,'user_context':user_context.get(obj['path'],''),'allow_manual_override':bool(allow_manual_override)})
        targets=[t for t in self.targets(documents) if not any(t['path'].casefold().startswith(b['path'].casefold()+'/') or t['path'].casefold()==b['path'].casefold() for b in bindings)]
        if not targets: raise SafetyError('没有可用的现有归属目录，请先建立所需目录')
        policy=self.app.policy.data
        semantic={k:policy[k] for k in ('categories','classification_rules','subcategory_rules','path_policy','task_status','explicit_preferences') if k in policy}
        if len(json.dumps(semantic,ensure_ascii=False))>24000: raise SafetyError('语义规则超出本次发送预算，请精简规则')
        if supplied:self.app.state.set('classification_notes',saved)
        return {'id':uid(),'created_at':now(),'policy_hash':self.app.policy.hash,'config':config,
            'bindings':bindings,'request':{'policy':semantic,'candidate_targets':targets,'documents':documents}}

    def run(self, packet):
        with instance_lock(self.app.state.profile):
            return self._run(packet)

    def _run(self, packet):
        config=self.settings()
        if not config['enabled']: raise SafetyError('AI审核已停用')
        if packet['policy_hash']!=self.app.policy.hash or packet['config']!=config: raise SafetyError('规则或AI设置已变化，请重新预览')
        from datetime import datetime
        if (datetime.fromisoformat(now())-datetime.fromisoformat(packet['created_at'])).total_seconds()>900: raise SafetyError('AI发送预览已过期')
        history=self.app.state.get('ai_history',[])
        if self.status()['calls_today']>=config['daily_limit']: raise SafetyError('已达到今日AI审核调用上限')
        for b in packet['bindings']:
            if self.app.state.get('classification_notes',{}).get(b['path'],'')!=b.get('user_context',''):raise SafetyError('补充说明已变化，请重新准备审核')
            if manifest(resolve_source(self.app.root,self.app.state,b['path']))!=b['manifest']: raise SafetyError('资料在发送前已变化，请重新预览')
            current=self.app.state.item(b['path'])
            if (current['id'] if current else None)!=b['item_id']: raise SafetyError('资料身份已变化，请重新预览')
            assignment=self.app.state.db.execute('SELECT version,data FROM assignments WHERE item_id=?',(b['item_id'],)).fetchone() if b['item_id'] else None
            if (assignment['version'] if assignment else 0)!=b['assignment_version'] or (json.loads(assignment['data']) if assignment else {})!=b['assignment']:
                raise SafetyError('归属已变化，请重新预览')
        provider=self.provider_factory(self.vault.read(),model=config['model'])
        record={'id':packet['id'],'at':now(),'model':config['model'],'items':len(packet['bindings']),'status':'failed','usage':{}}
        try:
            answer=provider.review(packet['request'])
            record['usage']=provider.usage
            rows=answer.get('items',[]); expected={b['id'] for b in packet['bindings']}
            if len(rows)!=len(expected) or any(not isinstance(r,dict) or not isinstance(r.get('id'),str) or r.get('id') not in expected for r in rows) or len({r['id'] for r in rows})!=len(expected):
                raise SafetyError('AI返回了重复、缺失或未知对象，本次不采用任何建议')
            answers={r['id']:r for r in rows}; targets={t['id']:t['path'] for t in packet['request']['candidate_targets']}
            results=[]
            for b in packet['bindings']:
                if self.app.state.get('classification_notes',{}).get(b['path'],'')!=b.get('user_context',''):raise SafetyError('审核期间补充说明已变化，旧结果未采用')
                response=answers[b['id']]; decision=response.get('decision')
                if decision not in ('confirm','needs_user_input','suggest_reclassify'): raise SafetyError('AI审核决定无效')
                reason=str(response.get('reason','')).strip()[:800]
                reason=re.sub(r'\bsk-[A-Za-z0-9_-]{10,}\b','[密钥已隐藏]',reason)
                if not reason: raise SafetyError('AI未提供判断依据')
                target=None
                if decision=='suggest_reclassify':
                    if not isinstance(response.get('target_id'),str):raise SafetyError('AI建议缺少有效目标标识')
                    target=targets.get(response.get('target_id'))
                    if not target: raise SafetyError('AI建议不在已发送的现有目录中')
                    self.app.policy.target(target,Path(b['path']).name)
                    if not safe_path(self.app.root,target).is_dir(): raise SafetyError('建议目录已不存在')
                    if b.get('storage_review'):
                        decision='needs_user_input';reason='资料位于用户指定的硬盘待迁区，调整存储计划需另行确认；'+reason
                    elif b.get('audit_mode') and target==str(Path(b['path']).parent).replace('\\','/'):
                        decision='confirm'
                    elif b['assignment'].get('manual_locked') and not b.get('allow_manual_override'):
                        decision='needs_user_input'; reason='人工指定归属已锁定，AI不能覆盖；'+reason
                if decision=='confirm' and ((not b['assignment'] and not b.get('allow_confirm_current')) or b['assignment'].get('destination_parent')==self.app.policy.data['review_path'] or b['path'].startswith(('00_收件箱/',EXTERNAL_PREFIX))):
                    decision='needs_user_input'; reason='尚无已确定的最终归属，需要选择目标；'+reason
                results.append((b,decision,reason,target))
            from .policy import Policy
            if Policy(self.app.root).hash!=packet['policy_hash']:raise SafetyError('AI审核期间规则已变化，建议未采用')
            if self.settings()!=packet['config']:raise SafetyError('AI审核期间设置已变化，建议未采用')
            for b in packet['bindings']:
                if manifest(resolve_source(self.app.root,self.app.state,b['path']))!=b['manifest']:raise SafetyError('AI审核期间资料已变化，建议未采用')
                current=self.app.state.item(b['path'])
                if (current['id'] if current else None)!=b['item_id']:raise SafetyError('AI审核期间资料身份已变化')
                a=self.app.state.db.execute('SELECT version,data FROM assignments WHERE item_id=?',(b['item_id'],)).fetchone() if b['item_id'] else None
                if (a['version'] if a else 0)!=b['assignment_version'] or (json.loads(a['data']) if a else {})!=b['assignment']:raise SafetyError('AI审核期间归属已变化，建议未采用')
            imported=[]
            for b,decision,reason,target in results:
                if manifest(resolve_source(self.app.root,self.app.state,b['path']))!=b['manifest']: raise SafetyError('AI审核期间文件已变化，建议未采用')
                ai_audit.save(self.app,b['path'],b['manifest'],config['model'],decision,reason,target,b.get('user_context',''))
                if b.get('allow_confirm_current') and decision=='confirm' and not b['assignment']:
                    old=self.app.state.db.execute("SELECT r.id,r.data FROM reviews r JOIN items i ON i.id=r.item_id WHERE i.path=? AND r.status IN ('pending','waiting_user','waiting_content','correction_required') ORDER BY r.rowid DESC LIMIT 1",(b['path'],)).fetchone()
                    if old:
                        data=json.loads(old['data']);data['version']=b['version'];data['result']={'decision':'confirm','reason':reason};data['reviewed_at']=now()
                        with self.app.state.db:self.app.state.db.execute("UPDATE reviews SET status='confirmed',data=? WHERE id=?",(json.dumps(data,ensure_ascii=False),old['id']))
                    imported.append({'source':b['path'],'review_id':old['id'] if old else None,'status':'confirmed','executed':False,'reason':reason});continue
                iid=self.app.state.observe(b['path'],b['manifest']['kind'],b['manifest'])
                active=self.app.state.db.execute("SELECT id,data FROM reviews WHERE item_id=? AND status IN ('pending','waiting_user','waiting_content','correction_required') ORDER BY rowid DESC LIMIT 1",(iid,)).fetchone()
                if active and json.loads(active['data']).get('version')!=b['version']:
                    with self.app.state.db:self.app.state.db.execute("UPDATE reviews SET status='superseded' WHERE id=?",(active['id'],))
                    active=None
                if not active:
                    with self.app.state.db:self.app.state.add_review(iid,b['version'],b['assignment'],'ai_requested','waiting_user',context_nonce=packet['id'])
                    active=self.app.state.db.execute('SELECT id FROM reviews WHERE item_id=? ORDER BY rowid DESC LIMIT 1',(iid,)).fetchone()
                result={'review_id':active['id'],'item_id':iid,'expected_version':b['version'],'assignment_version':b['assignment_version'],
                    'assignment_fingerprint':digest(b['assignment']),'decision':decision,'reason':reason}
                if target:result['target_parent']=target
                added=import_results(self.app.root,self.app.state,self.app.policy,{'schema_version':'1.0','policy_hash':packet['policy_hash'],'items':[result]},allow_locked=b.get('allow_manual_override',False))
                for row in added:row['source']=b['path']
                imported.extend(added)
            record.update(status='completed',results=imported)
            return {'results':imported,'usage':record['usage'],'model':config['model'],'files_moved':False}
        except SafetyError as exc:
            record['error']=str(exc); raise
        finally:
            record['usage']=getattr(provider,'usage',{})
            counters=self.app.state.get('ai_daily_counts',{});day=record['at'][:10]
            counters[day]=counters.get(day,sum(r['at'][:10]==day for r in history))+1
            self.app.state.set('ai_daily_counts',{d:counters[d] for d in sorted(counters)[-32:]})
            self.app.state.set('ai_history',(history+[record])[-200:])
