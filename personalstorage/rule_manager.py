"""Local drafts, shared-engine simulations and recoverable rule publication.

No file move, AI call, disk migration or synchronization is performed here.
"""
import copy
import json
from pathlib import Path
from .common import SafetyError,digest,now,uid,write_json,read_json,safe_path,instance_lock,manifest,linked
from .policy import Policy,FIELDS,OPS

KEY='rule_manager'
EDITABLE=('categories','subcategory_rules','classification_rules','explicit_preferences','tasks','executable_rules')


def model(config, previous=None):
    old={r['rule']['id']:r for r in (previous or {}).get('rules',[])}
    rules=[{'rule':copy.deepcopy(r),'enabled':True,'title':old.get(r['id'],{}).get('title',r['id']),
            'note':old.get(r['id'],{}).get('note','')} for r in config['executable_rules']]
    ids={r['rule']['id'] for r in rules}
    rules.extend(copy.deepcopy(r) for r in old.values() if not r['enabled'] and r['rule']['id'] not in ids)
    return {'config':copy.deepcopy(config),'rules':rules}


def recover(root,state):
    journal=state.get('rule_publish_journal')
    if not journal:return
    current=read_json(safe_path(root,'_整理规则/分类配置.json'));fingerprint=digest(current)
    if fingerprint==journal['new_hash']:
        state.set(KEY,journal['manager'])
        if journal.get('effective_changed',True):state.set('automation',None)
        state.set('rule_reading_pending',True)
    elif fingerprint!=journal['old_hash']:
        state.set('rule_recovery_warning','发布中断后发现外部规则修订；以有效配置为准，请比较草稿。')
    state.set('rule_publish_journal',None)


class RuleManager:
    def __init__(self,app):self.app=app;self.root=app.root;self.state=app.state

    def current(self):return Policy(self.root)

    def status(self):
        policy=self.current();saved=self.state.get(KEY,{})
        published=saved.get('published',{});same=published.get('hash')==policy.hash
        live=model(policy.data,published.get('model'))
        from .rule_package import template
        defaults=template()
        legacy=safe_path(self.root,'_整理规则/整理规则.md')
        legacy_text=legacy.read_text(encoding='utf-8-sig',errors='replace')[:20000] if legacy.is_file() and legacy.stat().st_size<128000 else ''
        base=next((h['model'] for h in saved.get('history',[]) if h['hash']==(saved.get('draft') or {}).get('base_hash')),live)
        return {'policy_hash':policy.hash,'policy_version':policy.data['policy_version'],'base_model':base,
                'defaults':{k:defaults.get(k,[]) for k in ('categories','subcategory_rules','classification_rules')},'legacy_document':legacy_text,
                'package':policy.data.get('shared_package',{}),'live':live,'draft':saved.get('draft'),
                'external_changed':bool(published and not same),'history':[{k:v for k,v in h.items() if k!='model'} for h in saved.get('history',[])],
                'reading_pending':self.state.get('rule_reading_pending',False),'warning':self.state.get('rule_recovery_warning'),
                'fields':sorted(FIELDS),'operators':sorted(OPS),'locked_constraints':policy.data['constraints']}

    def _load(self):
        policy=self.current();saved=copy.deepcopy(self.state.get(KEY,{}))
        if saved.get('published',{}).get('hash')!=policy.hash:
            m=model(policy.data,saved.get('published',{}).get('model'))
            entry={'id':uid(),'at':now(),'hash':policy.hash,'policy_version':policy.data['policy_version'],'source':'external' if saved else 'initial','summary':'外部有效配置' if saved else '首次纳管的有效配置','model':m}
            saved.setdefault('history',[]).insert(0,entry);saved['published']=entry
        return policy,saved

    def save(self,data,expected_revision=None,base_hash=None):
        with instance_lock(self.state.profile):
            policy,saved=self._load();old=saved.get('draft')
            if (old or {}).get('revision')!=expected_revision:raise SafetyError('草稿已被另一窗口修改，请刷新并比较')
            if base_hash!=policy.hash:raise SafetyError('有效规则已变化，请刷新并比较；未覆盖现有草稿')
            if not isinstance(data,dict) or not isinstance(data.get('config'),dict) or not isinstance(data.get('rules'),list):raise SafetyError('草稿格式无效')
            if len(json.dumps(data,ensure_ascii=False))>1500000:raise SafetyError('草稿超过大小预算')
            draft={'revision':uid(),'base_hash':policy.hash,'at':now(),'model':copy.deepcopy(data)}
            saved['draft']=draft;self.state.set(KEY,saved)
            return {'draft':draft,'validation':self.validate(draft['model'],policy)}

    def reset(self,revision=None,history_id=None):
        with instance_lock(self.state.profile):
            policy,saved=self._load()
            if (saved.get('draft') or {}).get('revision')!=revision:raise SafetyError('草稿版本已变化，请刷新')
            source=saved['published']
            if history_id:
                source=next((h for h in saved['history'] if h['id']==history_id),None)
                if source is None:raise SafetyError('历史版本已不可用')
            candidate=copy.deepcopy(source['model']);candidate['config']['policy_version']=policy.data['policy_version']
            saved['draft']={'revision':uid(),'base_hash':policy.hash,'at':now(),'model':candidate}
            self.state.set(KEY,saved);return {'draft':saved['draft'],'validation':self.validate(candidate,policy)}

    def rebase(self,revision,resolutions=None,edited=None,base_model=None,base_hash=None):
        with instance_lock(self.state.profile):
            policy,saved=self._load();draft=saved.get('draft')
            if (draft or {}).get('revision')!=revision:raise SafetyError('草稿已变化，请刷新后合并')
            ours=edited or (draft or {}).get('model')
            if not ours:raise SafetyError('没有待合并的草稿')
            original_hash=(draft or {}).get('base_hash') or base_hash
            base=next((h['model'] for h in saved['history'] if h['hash']==original_hash),None)
            if base is None:
                if not base_model or digest(base_model.get('config'))!=original_hash:raise SafetyError('草稿基础版本不可用，请手工对照')
                base=base_model
            theirs=saved['published']['model'];conflicts=[];missing=object();resolutions=resolutions or {}
            def key(value):
                if not isinstance(value,dict):return None
                return value.get('id') or value.get('root') or value.get('rule',{}).get('id')
            def merge(a,b,c,path):
                if b==a:return copy.deepcopy(c) if c is not missing else missing
                if c==a or b==c:return copy.deepcopy(b) if b is not missing else missing
                if all(isinstance(v,dict) for v in (a,b,c)):
                    out={}
                    for k in sorted(set(a)|set(b)|set(c)):
                        value=merge(a.get(k,missing),b.get(k,missing),c.get(k,missing),path+'/'+k)
                        if value is not missing:out[k]=value
                    return out
                if all(isinstance(v,list) and all(key(x) for x in v) for v in (a,b,c)):
                    maps=[{key(x):x for x in v} for v in (a,b,c)]
                    order=list(dict.fromkeys([key(x) for x in b+c]));out=[]
                    for k in order:
                        value=merge(*(m.get(k,missing) for m in maps),path+'/'+k)
                        if value is not missing:out.append(value)
                    return out
                choice=resolutions.get(path)
                if choice not in ('ours','theirs'):
                    conflicts.append({'field':path,'ours':None if b is missing else b,'theirs':None if c is missing else c})
                    return missing
                chosen=b if choice=='ours' else c
                return copy.deepcopy(chosen) if chosen is not missing else missing
            versions=[copy.deepcopy(v) for v in (base,ours,theirs)]
            for v in versions:v['config'].pop('executable_rules',None);v['config']['policy_version']=policy.data['policy_version']
            combined=merge(*versions,'')
            if conflicts:return {'conflicts':conflicts}
            combined['config']['executable_rules']=[r['rule'] for r in combined['rules'] if r['enabled']]
            new={'revision':uid(),'base_hash':policy.hash,'at':now(),'model':combined}
            saved['draft']=new;self.state.set(KEY,saved)
            return {'draft':new,'validation':self.validate(combined,policy),'conflicts':[]}

    def draft(self,revision):
        saved=self.state.get(KEY,{})
        draft=saved.get('draft')
        if not draft or draft['revision']!=revision:raise SafetyError('草稿已变化，请保存后重试')
        if draft['base_hash']!=self.current().hash:raise SafetyError('有效配置已变化，先比较并重新保存草稿')
        return draft

    def compile(self,data,policy):
        config=copy.deepcopy(data['config']);rules=data['rules']
        if len(rules)>300:raise SafetyError('第一版最多管理300条规则')
        for row in rules:
            if not isinstance(row,dict) or not isinstance(row.get('enabled'),bool) or not isinstance(row.get('rule'),dict):raise SafetyError('规则条目格式无效')
            if len(str(row.get('title','')))>120 or len(str(row.get('note','')))>2000:raise SafetyError('规则名称或备注过长')
        config['executable_rules']=[copy.deepcopy(r['rule']) for r in rules if r['enabled']]
        # Unknown/advanced fields survive unchanged. Only declared local editing surfaces may differ.
        before={k:v for k,v in policy.data.items() if k not in EDITABLE}
        after={k:v for k,v in config.items() if k not in EDITABLE}
        if before!=after:raise SafetyError('不能通过编辑器改变协议、版本、执行保护或目录骨架')
        cats=config.get('categories',[]);basecats=policy.data['categories']
        if len(cats)!=len(basecats):raise SafetyError('固定顶层类别不能增删')
        for a,b in zip(cats,basecats):
            if {k:v for k,v in a.items() if k not in ('purpose','boundary')}!={k:v for k,v in b.items() if k not in ('purpose','boundary')}:raise SafetyError('固定顶层名称和标识不可修改')
        base_sem={r['id']:r for r in policy.data.get('classification_rules',[])}
        current_sem=config.get('classification_rules',[])
        if len(current_sem)!=len(base_sem) or {r['id'] for r in current_sem}!=set(base_sem):raise SafetyError('判断顺序中的规则标识不能增删或重复')
        for r in current_sem:
            if r['id'].startswith('S') and r!=base_sem[r['id']]:raise SafetyError('安全与归档判断约定不可关闭或修改')
            if not isinstance(r.get('priority'),int):raise SafetyError('语义判断顺序需为整数')
        if [r['root'] for r in config.get('subcategory_rules',[])]!=[r['root'] for r in policy.data.get('subcategory_rules',[])]:raise SafetyError('细分规则的固定归属不可修改')
        Policy.from_data(self.root,config)
        return config

    def validate(self,data,policy=None):
        policy=policy or self.current();errors=[];warnings=[];tasks=[]
        try:
            config=self.compile(data,policy);candidate=Policy.from_data(self.root,config)
            allids=[];conditions={}
            for row in data['rules']:
                rule=row['rule'];ident=rule['id'];allids.append(ident)
                # Disabled definitions must remain valid, but unavailable destinations may be retained.
                Policy.from_data(self.root,{**config,'executable_rules':[rule]})
                if len(rule['all'])>20:raise SafetyError('每条规则最多20个条件')
                for c in rule['all']:
                    if c['op']=='contains' and not c['value'].strip():raise SafetyError('包含条件不能为空')
                    if len(json.dumps(c,ensure_ascii=False))>17000:raise SafetyError('条件值超过预算')
                try:
                    candidate.target(rule['target_parent'],'__rule_check__')
                    if not safe_path(self.root,rule['target_parent']).is_dir():raise SafetyError('目标目录不存在')
                except (SafetyError,OSError) as exc:
                    (errors if row['enabled'] else warnings).append(f"{ident}：{exc}")
                if row['enabled']:
                    key=digest([rule['all'],rule.get('level',100)])
                    if key in conditions and conditions[key]!=rule['target_parent']:errors.append(f'{ident}：相同条件和层级却指向不同目标')
                    conditions[key]=rule['target_parent']
                    if all(c['field'] in ('name','kind') for c in rule['all']):warnings.append(f'{ident}：仅依据名称或类型，需用样本检查归属是否充分')
            if len(allids)!=len(set(allids)):raise SafetyError('启用与停用规则的ID都必须唯一')
            ids=set()
            for task in config.get('tasks',[]):
                if not isinstance(task.get('id'),str) or not task['id'].strip() or task['id'] in ids:raise SafetyError('任务ID为空或重复')
                ids.add(task['id']);issue=None
                if task.get('lifecycle') not in ('active','unspecified','completed','archived'):raise SafetyError('任务状态无效')
                if task.get('storage') not in ('active','staging','offline'):raise SafetyError('任务存储状态无效')
                try:
                    p=safe_path(self.root,task['path'])
                    if task['path'].split('/')[0] not in candidate.categories or task['path'].startswith(('00_','99_','_整理规则')):raise SafetyError('任务目录不在允许范围')
                    if not p.is_dir():raise SafetyError('登记目录不存在')
                    suffix='completed' if p.name.endswith('（已完成）') else 'archived' if p.name.endswith('（已归档）') else None
                    if (suffix or task['lifecycle'] in ('completed','archived')) and suffix!=task['lifecycle']:raise SafetyError('登记状态与目录后缀不一致，请核对')
                except (SafetyError,OSError) as exc:issue=str(exc);warnings.append(f"任务 {task['id']}：{issue}")
                tasks.append({'id':task['id'],'path':task['path'],'issue':issue})
            prefs=config.get('explicit_preferences',[])
            if not isinstance(prefs,(list,dict,str)):raise SafetyError('长期偏好格式无效')
            pref_ids=set()
            if isinstance(prefs,list):
                for pref in prefs:
                    if isinstance(pref,str):continue  # Preserve pre-existing text preferences.
                    if not isinstance(pref,dict):raise SafetyError('偏好条目需为文字或结构化对象')
                    if 'description' in pref:
                        if not isinstance(pref.get('id'),str) or not pref['id'] or pref['id'] in pref_ids:raise SafetyError('偏好ID为空或重复')
                        pref_ids.add(pref['id'])
                        if not isinstance(pref['description'],str) or not pref['description'].strip():raise SafetyError('偏好说明不能为空')
                        if any(not isinstance(pref.get(k,''),str) for k in ('scope','exception','evidence')):raise SafetyError('偏好范围、例外和依据需为文字')
            semantic={k:config[k] for k in ('categories','classification_rules','subcategory_rules','path_policy','task_status','explicit_preferences') if k in config}
            if len(json.dumps(semantic,ensure_ascii=False))>24000:errors.append('分类约定超过AI发送预算，请精简后发布')
            changed=any(config.get(k)!=policy.data.get(k) for k in ('executable_rules','tasks'))
            return {'valid':not errors,'errors':errors,'warnings':warnings,'tasks':tasks,'requires_test':changed,'effective_changed':digest(config)!=policy.hash}
        except (SafetyError,KeyError,TypeError,ValueError,AttributeError) as exc:
            return {'valid':False,'errors':[str(exc)],'warnings':warnings,'tasks':tasks,'requires_test':True}

    def simulate(self,revision,samples):
        draft=self.draft(revision);policy=self.current();report=self.validate(draft['model'],policy)
        if not report['valid']:raise SafetyError('草稿校验未通过：'+'；'.join(report['errors']))
        if not isinstance(samples,list) or not 1<=len(samples)<=10:raise SafetyError('每次请选择1至10个样本')
        candidate=Policy.from_data(self.root,self.compile(draft['model'],policy));results=[]
        from .extraction import extract,signature
        for sample in samples:
            context=sample.get('context') or {}
            if set(context)-{'task_id','purpose'} or any(not isinstance(v,str) or len(v)>2000 for v in context.values()):raise SafetyError('样本上下文无效')
            if 'path' in sample:
                from .planner import in_scope
                relative=sample['path']
                if not (relative.startswith('@inbox/') or in_scope(relative,True,True,True)) or ('/' not in relative and Path(relative).suffix.lower()=='.lnk'):raise SafetyError('样本不在允许范围')
                path=__import__('personalstorage.inboxes',fromlist=['resolve']).resolve(self.root,self.state,relative)
                before=manifest(path);features=extract(path)
                if manifest(path)!=before:raise SafetyError('试跑期间样本内容变化，请重试')
                binding={'path':relative,'fingerprint':digest(before)}
            else:
                features=sample.get('features',{})
                if set(features)-{'name','text','kind'} or any(not isinstance(v,str) for v in features.values()):raise SafetyError('模拟特征无效')
                if not features.get('name') or len(features.get('text',''))>16000 or features.get('kind') not in ('file','directory'):raise SafetyError('请填写模拟名称、类型和预算内正文')
                binding={'synthetic':True}
            def explain(p):
                trace=p.explain(features,context);decision=trace['result'];issues=[]
                if decision['decision']=='move':
                    try:
                        target=p.target(decision['target_parent'],features['name'])
                        if not safe_path(self.root,decision['target_parent']).is_dir():issues.append('目标目录不存在')
                        if safe_path(self.root,target).exists():issues.append('目标同名或样本已在目标位置；实际执行将再检查')
                    except (SafetyError,OSError) as exc:issues.append(str(exc))
                return {**trace,'target_issues':issues}
            results.append({'name':features['name'],'binding':binding,'text_source':features.get('text_source','native' if features.get('text') else 'none'),
                            'warnings':features.get('warnings',[]),'ocr':features.get('ocr'),'excerpt':features.get('text','')[:1200],
                            'current':explain(policy),'draft':explain(candidate)})
        with instance_lock(self.state.profile):
            self.draft(revision);saved=self.state.get(KEY)
            saved['draft']['test']={'at':now(),'signature':signature(),'count':len(results),'bindings':[r['binding'] for r in results],
                                    'changed':sum(r['current']['result']!=r['draft']['result'] for r in results)}
            self.state.set(KEY,saved)
        return {'results':results,'test':saved['draft']['test'],'ai_called':False,'files_moved':False}

    def preview(self,revision):
        draft=self.draft(revision);policy=self.current();validation=self.validate(draft['model'],policy)
        if not validation['valid']:raise SafetyError('校验未通过：'+'；'.join(validation['errors']))
        if validation['requires_test'] and not draft.get('test'):raise SafetyError('执行规则或任务绑定有变化，请先完成一次样本试跑')
        self.check_test(draft)
        config=self.compile(draft['model'],policy)
        changes=[{'field':k,'before':policy.data.get(k),'after':config.get(k)} for k in EDITABLE if config.get(k)!=policy.data.get(k)]
        previous=self.state.get(KEY,{}).get('published',{}).get('model',model(policy.data))
        metadata_changes=None if previous['rules']==draft['model']['rules'] else {'before':previous['rules'],'after':draft['model']['rules']}
        return {'revision':revision,'policy_hash':policy.hash,'model_hash':digest(draft['model']),'changes':changes,'metadata_changes':metadata_changes,
                'validation':validation,'test':draft.get('test'),'metadata_only':not changes,'original_documents_touched':False}

    def check_test(self,draft):
        test=draft.get('test')
        if not test:return
        from .extraction import signature
        from .inboxes import resolve
        if test['signature']!=signature():raise SafetyError('文字提取设置已变化，请重新试跑')
        for binding in test['bindings']:
            if not binding.get('synthetic') and digest(manifest(resolve(self.root,self.state,binding['path'])))!=binding['fingerprint']:raise SafetyError('试跑样本已变化，请重新试跑')

    def publish(self,ticket):
        with instance_lock(self.state.profile):
            draft=self.draft(ticket['revision']);plan=self.preview(draft['revision'])
            if any(plan[k]!=ticket[k] for k in ('policy_hash','model_hash')):raise SafetyError('发布预览已过期，请重新比较')
            if self.state.db.execute("SELECT COUNT(*) FROM operations WHERE status IN ('prepared','moved','verified','recovery_required')").fetchone()[0]:raise SafetyError('存在未完成文件操作，请先恢复记录')
            policy,saved=self._load()
            if policy.hash!=plan['policy_hash']:raise SafetyError('发布期间有效配置已变化，请重新比较')
            data=self.compile(draft['model'],policy);changed=digest(data)!=policy.hash
            if changed:data['policy_version']='local-'+now()[:19].replace('-','').replace(':','').replace('T','-')+'-'+uid()[:6]
            published_model=copy.deepcopy(draft['model']);published_model['config']=data
            entry={'id':uid(),'at':now(),'hash':digest(data),'policy_version':data['policy_version'],'source':'editor','summary':'；'.join(c['field'] for c in plan['changes']) or '仅管理信息更新','model':published_model,'test':draft.get('test')}
            backup=self.state.profile/'rule-backups'/entry['id'];backup.mkdir(parents=True)
            write_json(backup/'classification-config.json',policy.data);write_json(backup/'manager.json',saved)
            saved['history']=[entry]+saved.get('history',[])[:49];saved['published']=entry;saved['draft']=None
            journal={'old_hash':policy.hash,'new_hash':entry['hash'],'manager':saved,'effective_changed':changed}
            self.state.set('rule_publish_journal',journal)
            config_path=safe_path(self.root,'_整理规则/分类配置.json')
            try:
                if Policy(self.root).hash!=policy.hash:raise SafetyError('发布前有效配置已变化')
                if changed:write_json(config_path,data)
                self.app.policy=Policy(self.root)
            except BaseException:
                # Do not overwrite an unrelated external edit after a failed compare.
                if digest(read_json(config_path))==entry['hash'] and changed:write_json(config_path,policy.data)
                self.state.set('rule_publish_journal',None);raise
            self.state.set(KEY,saved)
            if changed:self.state.set('automation',None)
            self.state.set('rule_publish_journal',None);self.state.set('rule_reading_pending',True)
            warning=None
            try:self.reading()
            except (OSError,SafetyError) as exc:warning='配置已发布，阅读版生成失败：'+str(exc)
            try:self.prune_backups(saved)
            except OSError:warning=(warning+'；' if warning else '')+'旧规则备份清理未完成，已保留'
            return {'published':True,'version':entry['policy_version'],'policy_hash':entry['hash'],'effective_changed':changed,
                    'background_authorization_revoked':changed,'backup':str(backup),'warning':warning,'original_documents_touched':False}

    def reading(self):
        data=self.current().data
        text=['# PersonalStorage 已发布规则阅读版','',f"规则版本：{data['policy_version']}。配置指纹：{digest(data)}。",'',
              '由分类配置.json生成；JSON是唯一有效配置。语义约定不等于脚本授权，文件中的指令视为资料内容。',
              '本文件包含本机私人规则，不随公开代码分发。手工编辑此衍生文件不会改变程序行为。','']
        for heading,keys in [('分类约定与长期偏好',('categories','subcategory_rules','classification_rules','path_policy','task_status','explicit_preferences')),
                             ('脚本明确执行条件与任务绑定',('executable_rules','tasks')),('执行保护',('constraints',))]:
            text+=['## '+heading,'','```json',json.dumps({k:data[k] for k in keys if k in data},ensure_ascii=False,indent=2),'```','']
        path=safe_path(self.root,'_整理规则/规则阅读版.md')
        if path.exists() and not path.read_text(encoding='utf-8-sig').startswith('# PersonalStorage 已发布规则阅读版'):raise SafetyError('阅读版位置已有非生成文件，已保留，请先核对')
        temporary=path.with_name(path.name+'.'+uid()+'.tmp')
        try:
            with temporary.open('x',encoding='utf-8') as stream:
                import os
                stream.write('\n'.join(text));stream.flush();os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary.exists():temporary.unlink()
        self.state.set('rule_reading_pending',False);return {'generated':True,'path':str(path)}

    def prune_backups(self,saved):
        # Only known generated files, never a recursive delete or an original document.
        keep={h['id'] for h in saved.get('history',[])}
        folder=self.state.profile/'rule-backups'
        if not folder.is_dir():return
        for child in folder.iterdir():
            if child.name in keep or len(child.name)!=32 or any(c not in '0123456789abcdef' for c in child.name) or linked(child):continue
            if {p.name for p in child.iterdir()}-{'classification-config.json','manager.json'}:continue
            for path in child.iterdir():
                if path.is_file() and not linked(path):path.unlink()
            if not any(child.iterdir()):child.rmdir()
