"""Loopback desktop adapter with separately authorized DeepSeek reviews."""
import argparse
import hmac
import json
import os
import secrets
import threading
import time
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .service import Application
from .common import SafetyError, safe_path, manifest, digest, uid, now, linked
from .extraction import extract
from . import reports
from .recovery import undo
from .review import resolve_external, review_context
from .ai import AIManager
from . import __version__
from . import activity, scanning, inboxes, importing, classification, ai_audit,overview,ocr
from .rule_manager import RuleManager

STATIC = Path(__file__).with_name('web_static')


class Desktop:
    def __init__(self, root, profile=None):
        self.root = Path(root).absolute()
        self.profile = profile
        with self.app() as app:
            self.profile = app.state.profile
        self.token = secrets.token_urlsafe(32)
        self.objects = {}
        self.previews = {}
        self.jobs = {}
        self.job_stops={}
        self.request_jobs={};self.request_guard=threading.Lock()
        self.downloads = {}
        self.ai_packets = {}
        self.scan_cache = {}
        self.audit_stop=threading.Event()
        self.audit_guard=threading.Lock()
        self.credential_vault = None
        self.ai_provider_factory = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='storage-ui')
        self.read_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='storage-overview')
        self.search_stops={};self.graph_handles={}
        self.stop = threading.Event()
        self.watch = None
        self.watch_error = None
        self.serial = threading.RLock()
        self.url = None

    def app(self):
        from contextlib import contextmanager
        @contextmanager
        def opened():
            app = Application(self.root, getattr(self, 'profile', None))
            try:
                yield app
            finally:
                app.close()
        return opened()

    def object(self, path, **extra):
        ident = uid()
        self.objects[ident] = {'path': path, **extra}
        return ident

    def get(self, ident):
        if not isinstance(ident, str) or ident not in self.objects:
            raise SafetyError('对象已失效，请刷新列表')
        return self.objects[ident]

    def graph_node(self,node):
        if not node.get('available'):return node
        key=(node['path'],*node['identity'])
        ident=self.graph_handles.get(key)
        if not ident:
            ident=self.object(node['path'],overview=True,node_identity=node['identity']);self.graph_handles[key]=ident
        return {**node,'id':ident}

    def graph_links(self,app):
        return [{**link,'source':self.graph_node(link['source']),'target':self.graph_node(link['target'])} for link in overview.links(app.root,app.state)]

    def graph_children(self,app,ident=None,files=False,offset=0):
        relative=overview.check_node(app.root,self.get(ident))['path'] if ident else ''
        data=overview.children(app.root,relative,files,offset)
        return {**data,'parent':self.graph_node(data['parent']),'nodes':[self.graph_node(n) for n in data['nodes']]}

    def graph_search(self,body):
        # A separate, cancelable read queue cannot be blocked by model calls or moves.
        for event in list(self.search_stops.values()):event.set()
        stop=threading.Event();ident=uid();self.search_stops[ident]=stop
        self.jobs[ident]={'id':ident,'state':'queued','phase':'等待名称搜索','page':'overview','action':'overview-search'}
        with self.app() as app:activity.begin(app.state,ident,'overview-search','overview')
        def run():
            task=self.jobs[ident];task.update(state='running',phase='搜索名称与路径')
            try:
                result=overview.search(self.root,body.get('query'),body.get('files') is True,stop,lambda n:task.update(phase=f'已读取 {n} 个目录项'))
                result['nodes']=[self.graph_node(n) for n in result['nodes']]
                with self.app() as app:activity.update(app.state,ident,'done',result=result)
                task.update(state='done',phase='搜索已停止' if result['stopped'] else '搜索完成',result=result)
            except Exception as exc:
                with self.app() as app:activity.update(app.state,ident,'error',error=exc)
                task.update(state='error',phase='搜索未完成',error=str(exc))
            finally:self.search_stops.pop(ident,None)
        self.read_pool.submit(run);return {'job_id':ident}

    def job(self,action,body):
        request=body.get('_request_id')
        if request is None:return self._new_job(action,body)
        import re
        if not isinstance(request,str) or not re.fullmatch('[a-zA-Z0-9_-]{16,64}',request):raise SafetyError('请求标识无效')
        fingerprint=digest([action,body])
        with self.request_guard:
            previous=self.request_jobs.get(request)
            if previous:
                if previous['fingerprint']!=fingerprint:raise SafetyError('同一请求标识不能用于不同操作')
                return {'job_id':previous['id']}
            result=self._new_job(action,body);self.request_jobs[request]={'id':result['job_id'],'fingerprint':fingerprint,'at':time.monotonic()}
            for key,value in list(self.request_jobs.items()):
                if time.monotonic()-value['at']>3600 and self.jobs.get(value['id'],{}).get('state') not in ('queued','running'):self.request_jobs.pop(key,None)
            return result

    def _new_job(self, action, body):
        audit=action=='library-audit'
        if audit:
            if not self.audit_guard.acquire(blocking=False):raise SafetyError('全库复核已在运行或排队')
            self.audit_stop.clear()
        ident = uid()
        page=body.get('_page')
        stop_event=threading.Event();self.job_stops[ident]=stop_event
        safe_retry=action in ('scan','library-audit','wb-index')
        self.jobs[ident] = {'id': ident, 'state': 'queued', 'phase': '等待处理', 'at': now(), 'page':page,'action':action}
        try:
            with self.app() as app:activity.begin(app.state,ident,action,page)
            if safe_retry:
                with self.app() as app:
                    retries=app.state.get('job_retries',{});retries[ident]={'action':action,'page':page,'at':now(),'owner_pid':os.getpid(),'status':'queued','policy_hash':app.policy.hash,'body':{k:body[k] for k in ('include_root','use_ai','context','max_batches','budget') if k in body}}
                    app.state.set('job_retries',dict(list(retries.items())[-30:]))
        except BaseException:
            if audit:self.audit_guard.release()
            raise
        def work():
            job = self.jobs[ident]
            job.update(state='running', phase='核对与处理')
            outcome={'state':'error','phase':'未完成','error':'任务未正常结束'}
            try:
                from .tasks import scope,checkpoint
                with self.serial, self.app() as app,scope(stop_event):
                    checkpoint()
                    activity.update(app.state,ident,'running',phase=job['phase'])
                    result = self.perform(app, action, body, job)
                    activity.update(app.state,ident,'done',phase='已完成',result=result)
                outcome={'state':'done','phase':'已完成','result':result}
            except Exception as exc:
                outcome={'state':'error','phase':'未完成','error':str(exc)}
                from .tasks import Cancelled
                if isinstance(exc,Cancelled):outcome.update(cancelled=True,phase='已停止')
                try:
                    with self.app() as app:activity.update(app.state,ident,'interrupted' if outcome.get('cancelled') else 'error',phase='已停止' if outcome.get('cancelled') else '未完成',error=str(exc))
                except Exception:outcome['error']+='；本次任务记录未能保存'
            finally:
                if safe_retry:
                    try:
                        with self.app() as app:
                            retries=app.state.get('job_retries',{})
                            if ident in retries:
                                retries[ident]['status']='completed' if outcome['state']=='done' else 'interrupted';app.state.set('job_retries',retries)
                    except Exception:pass
                self.job_stops.pop(ident,None)
                if audit:self.audit_guard.release()
                # Once terminal status is visible, records and locks are ready
                # for an immediate retry from another HTTP request.
                job.update(outcome)
        self.pool.submit(work)
        return {'job_id': ident}

    def bootstrap(self):
        with self.app() as app:
            s = app.status()
            s.update(watching=bool(self.watch and self.watch.is_alive()), watch_error=self.watch_error)
            auth = app.state.get('automation')
            ai = AIManager(app, vault=self.credential_vault).status()
            return {'app_version':__version__,'status': s, 'policy': app.policy.data, 'policy_hash': app.policy.hash, 'ai':ai,'library_audit':app.state.get('library_audit'),'ui_mode':'desktop' if getattr(self,'native_mode',False) else 'web',
                    'authorization_current': bool(auth and auth['policy_hash'] == app.policy.hash),
                    'authorization_scope_root': bool(auth and auth.get('include_root')),
                    'model_enabled': ai['settings']['enabled'] and ai['configured'],'ocr':ocr.status()}

    def pending(self, history=False):
        with self.app() as app:
            out = reports.resolved_reviews(app.state) if history else reports.pending(app.state)
            for r in out:
                r['user_context']=app.state.get('classification_notes',{}).get(r['path'],'')
                context = review_context({'id': r['review_id'], 'path': r['path'],
                    'status': r['status'], 'data': json.dumps(r['details'], ensure_ascii=False)})
                r['id'] = self.object(r['path'], review_id=r['review_id'], review_context_hash=context)
                try:
                    actual=inboxes.resolve(self.root,app.state,r['path'],enabled=False)
                    r['available']=actual.exists();r['absolute_path']=str(actual)
                    if r['path'].startswith(inboxes.PREFIX):r['external']=True;r['source_label']=inboxes.source_record(app.state,r['path'].split('/')[1],enabled=False)['label']
                except SafetyError:
                    r['available'] = False
                if not r['available'] and not history:
                    r['recorded_status'] = r['status']
                    r['status'] = 'waiting_content'
            return out

    def operations(self):
        with self.app() as app:
            out = reports.operations(app.state, 500)
            for r in out:
                # Public row contains no multi-megabyte member manifest.
                m = r['data'].pop('manifest', {})
                r['file_count'] = len(m.get('files', []))
                r['id_handle'] = self.object(r['data'].get('target', r['data']['source']), operation_id=r['id'])
            return out

    def history(self, offset=0, query='', status='all'):
        if offset<0 or offset>100000 or len(query)>200:raise SafetyError('记录查询范围无效')
        with self.app() as app:
            result=activity.recent(app.state,offset=offset,query=query,status=status)
            for row in result['rows']:
                if row['kind']=='operation':row['id_handle']=self.object(row['data'].get('target',row['data']['source']),operation_id=row['id'])
                else:row['id_handle']='task-'+row['id']
            return result

    def directory(self, ident=None,allow_archived=False):
        with self.app() as app:
            parent = self.get(ident)['path'] if ident else None
            paths = [p for p in safe_path(self.root, parent).iterdir()] if parent else [self.root / p for p in sorted(app.policy.categories - {'00_收件箱', '09_移动硬盘管理'})]
            out = []
            for p in sorted(paths, key=lambda x: x.name.casefold()):
                if not p.is_dir() or linked(p):
                    continue
                rel = p.relative_to(self.root).as_posix()
                try:
                    app.policy.target(rel, '__target_check__',allow_archived=allow_archived)
                except SafetyError:
                    continue
                out.append({'id': self.object(rel, directory=True), 'name': p.name, 'path': rel})
            return {'path': parent or '', 'items': out}

    def download(self, path):
        ident = uid()
        self.downloads[ident] = Path(path)
        return {'download': '/download/' + ident, 'filename': Path(path).name}

    def rule_samples(self,ident=None,offset=0):
        from itertools import islice
        from .discovery import eligible_name
        with self.app() as app:
            obj=self.get(ident) if ident else None
            if obj and not obj.get('rule_browser'):raise SafetyError('样本浏览对象无效')
            logical=obj['path'] if obj else ''
            if obj and obj.get('source_root'):
                parent=inboxes.source_root(app.root,app.state,obj['source_root'])
            else:parent=inboxes.resolve(app.root,app.state,logical) if logical else app.root
            if not parent.is_dir() or linked(parent):raise SafetyError('样本目录不可用')
            if obj and obj.get('identity')!=[parent.stat().st_dev,parent.stat().st_ino]:raise SafetyError('样本目录身份已变化')
            entries=list(islice(parent.iterdir(),2001));limited=len(entries)>2000
            rows=[]
            for path in sorted(entries[:2000],key=lambda p:(not p.is_dir(),p.name.casefold())):
                relative=logical+'/'+path.name if logical else path.name
                if linked(path) or not eligible_name(path.name) or path.name.casefold()=='agents.md':continue
                if not logical and (path.name.startswith(('_整理规则','09_移动硬盘管理')) or path.suffix.lower()=='.lnk'):continue
                if not logical and path.is_dir() and path.name not in app.policy.categories:continue
                st=path.stat();kind='directory' if path.is_dir() else 'file'
                rows.append({'id':self.object(relative,rule_browser=True,identity=[st.st_dev,st.st_ino]),'name':path.name,'path':relative,
                             'kind':kind,'sampleable':bool(logical or kind=='file')})
            if not logical:
                for source in inboxes.settings(app.state)['sources']:
                    if not source['enabled']:continue
                    try:
                        path=inboxes.source_root(app.root,app.state,source['id']);st=path.stat()
                        rows.append({'id':self.object(inboxes.PREFIX+source['id'],rule_browser=True,source_root=source['id'],identity=[st.st_dev,st.st_ino]),
                                     'name':source['label']+'（外部收件箱）','path':inboxes.PREFIX+source['id'],'kind':'directory','sampleable':False})
                    except (SafetyError,OSError):pass
            offset=max(0,offset)
            return {'path':logical,'items':rows[offset:offset+100],'next_offset':offset+100 if offset+100<len(rows) else None,'limited':limited}

    def public_plan(self, plan):
        return {'id': plan['id'], 'created_at': plan['created_at'], 'actions': [
            {k: v for k, v in a.items() if k != 'manifest'} for a in plan['actions']]}

    def perform(self, app, action, b, job):
        if action.startswith('wb-'):
            from .workbench import perform
            return perform(self,app,action,b,job)
        if action.startswith('rules-'):
            manager=RuleManager(app)
            if action=='rules-save':return manager.save(b['model'],b.get('revision'),b.get('base_hash'))
            if action=='rules-reset':return manager.reset(b.get('revision'),b.get('history_id'))
            if action=='rules-rebase':return manager.rebase(b.get('revision'),b.get('resolutions'),b.get('model'),b.get('base_model'),b.get('base_hash'))
            if action=='rules-validate':return manager.validate(manager.draft(b['revision'])['model'])
            if action=='rules-simulate':
                samples=[]
                for sample in b.get('samples',[]):
                    if 'id' in sample:
                        obj=self.get(sample['id'])
                        if not obj.get('rule_browser') or obj.get('source_root'):raise SafetyError('请选择受控文件或完整资料包')
                        path=inboxes.resolve(app.root,app.state,obj['path']);st=path.stat()
                        if path.is_dir() and '/' not in obj['path']:raise SafetyError('请进入分类后选择具体资料包')
                        if [st.st_dev,st.st_ino]!=obj['identity']:raise SafetyError('样本已被替换，请重新选择')
                        samples.append({'path':obj['path'],'context':sample.get('context',{})})
                    else:
                        if set(sample)-{'features','context'}:raise SafetyError('模拟样本不接受任意路径')
                        samples.append(sample)
                job['phase']='本地提取与规则试跑，不调用AI';return manager.simulate(b['revision'],samples)
            if action=='rules-preview':
                result=manager.preview(b['revision']);ticket=uid();self.previews[ticket]={'rule_publish':result,'at':time.monotonic()}
                return {**result,'ticket':ticket}
            if action=='rules-publish':
                preview=self.previews.get(b.get('ticket'),{})
                if 'rule_publish' not in preview or time.monotonic()-preview['at']>900:raise SafetyError('发布预览已过期，请重新查看差异')
                result=manager.publish(preview['rule_publish']);self.previews.pop(b['ticket'],None);self.scan_cache.clear();return result
            if action=='rules-reading':
                from .common import instance_lock
                with instance_lock(app.state.profile):return manager.reading()
            raise SafetyError('未知规则管理操作')
        if action=='ocr-configure':
            result=ocr.configure(b);self.scan_cache.clear();return result
        if action=='ocr-clear-cache':
            result=ocr.clear_cache();self.scan_cache.clear();return result
        if action=='ocr-test':
            job['phase']='本机合成图片离线识别自检';return ocr.self_test()
        if action=='inbox-add':return inboxes.add(app.root,app.state,b.get('path'),b.get('label',''),b.get('import_mode','copy'))
        if action=='inbox-mode':return inboxes.set_mode(app.state,b.get('id'),b.get('import_mode'))
        if action=='inbox-toggle':return inboxes.toggle(app.state,b.get('id'),b.get('enabled'))
        if action=='inbox-remove':return inboxes.remove(app.state,b.get('id'))
        if action=='cleanup-preview':
            ticket=importing.cleanup_preview(app,self.get(b['id']).get('operation_id'))
            ident=uid();self.previews[ident]={'cleanup':ticket,'time':time.monotonic(),'used':False}
            return {'ticket':ident,**{k:ticket[k] for k in ('source_absolute','target','file_count')}}
        if action=='cleanup-execute':
            ticket=self.previews.get(b.get('ticket'))
            if not ticket or not ticket.get('cleanup') or ticket['used'] or time.monotonic()-ticket['time']>900:raise SafetyError('清理预览已失效，请重新核对')
            ticket['used']=True
            return importing.cleanup_execute(app,ticket['cleanup'])
        if action=='library-audit':
            if b.get('confirmed') is not True:raise SafetyError('请通过全库复核按钮确认范围与批次上限后启动')
            manager=AIManager(app,vault=self.credential_vault,provider_factory=self.ai_provider_factory)
            return ai_audit.run(app,manager,job,self.audit_stop,b.get('max_batches',20))
        if action.startswith('ai-'):
            manager=AIManager(app, vault=self.credential_vault, provider_factory=self.ai_provider_factory)
            if action=='ai-configure':return manager.configure(b)
            if action=='ai-test':return manager.test()
            if action=='ai-clear-key':return manager.clear_key()
            if action=='ai-prepare':
                ids=b.get('ids')
                if not isinstance(ids,list) or not ids or len(ids)>10:raise SafetyError('请选择1至10项资料')
                contexts=b.get('user_context',{})
                packet=manager.prepare([self.get(i) for i in ids],contexts,allow_manual_override=b.get('override_locked') is True)
                self.ai_packets[packet['id']]=packet
                return {'ticket':packet['id'],'model':packet['config']['model'],'with_content':packet['config']['with_content'],
                    'target_count':len(packet['request']['candidate_targets']),
                    'items':[{'name':d['name'],'source':d['source'],'excerpt':d['text'],'members':d['members'],'warnings':d['warnings'],'user_context':d.get('user_context','')} for d in packet['request']['documents']]}
            if action=='ai-run':
                packet=self.ai_packets.pop(b.get('ticket'),None)
                if not packet:raise SafetyError('AI发送预览已失效，请重新选择')
                job['phase']='DeepSeek正在审核，等待结构化结果'
                result=manager.run(packet)
                for obj in list(self.objects.values()):
                    if obj.get('path') in {item['path'] for item in packet['bindings']} and not obj.get('directory'):classification.promote(app,obj)
                return result
            if action=='ai-adopt':
                obj=self.get(b['id'])
                row=app.state.db.execute('SELECT r.*,i.path FROM reviews r JOIN items i ON i.id=r.item_id WHERE r.id=?',(obj.get('review_id'),)).fetchone()
                if not row or row['status']!='correction_required' or review_context(row)!=obj.get('review_context_hash'):raise SafetyError('建议已变化，请刷新待办')
                data=json.loads(row['data']);result=data.get('result',{})
                if result.get('decision')!='suggest_reclassify':raise SafetyError('没有可采用的归属建议')
                if digest(manifest(inboxes.resolve(app.root,app.state,obj['path'])))!=data['version']:raise SafetyError('资料已变化，请重新审核')
                parent=result['target_parent']
                if not safe_path(app.root,parent).is_dir():raise SafetyError('建议目录已不存在')
                self.perform(app,'detail',b,job)
                classification.promote(app,obj)
                return self.perform(app,'preview',{'ids':[b['id']],'target_id':self.object(parent,directory=True),
                    'evidence':'用户采用AI建议：'+result.get('reason','')},job)
        if action == 'scan':
            context=b.get('context') or {}
            if set(context)-{'task_id','purpose'} or any(not isinstance(v,str) or len(v)>2000 for v in context.values()):raise SafetyError('本次投递上下文无效')
            scan_started=time.monotonic()
            job['phase'] = '读取收件箱与必要的本地文字识别，生成建议'
            def progress(completed,total):job.update(completed=completed,total=total)
            result=scanning.scan(app.root,app.policy,self.scan_cache,include_root=b.get('include_root') is True,progress=progress,context=context) if inboxes.settings(app.state)['internal_enabled'] else {'rows':[],'scanned_at':now(),'elapsed_ms':0}
            external=inboxes.scan_external(app.root,app.state,app.policy,self.scan_cache,context=context)
            result['rows'].extend(external['rows']);result.update(source_errors=external['source_errors'],already_imported=external['already_imported'])
            rows = [];objects=[]
            for a in result['rows']:
                ident = self.object(a['source'], fingerprint=None, scan_fingerprint=a.get('scan_fingerprint'), action=a,
                                    policy_hash=app.policy.hash, include_root=b.get('include_root') is True,context=context)
                rows.append({'id':ident,**a})
                objects.append(self.get(ident))
            if b.get('use_ai') is True:
                result['classification']=classification.fallback(app,AIManager(app,vault=self.credential_vault,provider_factory=self.ai_provider_factory),rows,objects,job)
            return {**result,'rows':rows,'elapsed_ms':round((time.monotonic()-scan_started)*1000)}
        if action == 'detail':
            obj = self.get(b['id']); path = inboxes.resolve(self.root,app.state,obj['path'])
            m = manifest(path); obj['fingerprint'] = digest(m)
            result = {'path': obj['path'], 'absolute_path': str(path), 'fingerprint': digest(m),
                      'features': extract(path, limit=8000,force=b.get('force_ocr') is True), 'file_count': len(m['files']),
                      'members': [f['path'] for f in m['files'][:80]]}
            if obj.get('review_id'):
                row = app.state.db.execute('SELECT r.*,a.version AS av,a.data AS ad FROM reviews r LEFT JOIN assignments a ON a.item_id=r.item_id WHERE r.id=?', (obj['review_id'],)).fetchone()
                if not row: raise SafetyError('待办不存在')
                assignment = json.loads(row['ad']) if row['ad'] else {}
                obj['review_context'] = {'review_id': row['id'], 'item_id': row['item_id'],
                    'expected_version': digest(m), 'assignment_version': row['av'] or 0,
                    'assignment_fingerprint': digest(assignment)}
                obj['assignment_fingerprint'] = digest(assignment)
                result['assignment'] = assignment
            return result
        if action == 'preview':
            from .common import write_json
            ids=b.get('ids',[])
            if not isinstance(ids,list) or not ids or len(ids)>500 or len(set(ids))!=len(ids):raise SafetyError('请选择1至500个不同对象')
            objs=[self.get(i) for i in ids];manual=bool(b.get('target_id'));waiting=b.get('waiting') is True
            if manual and len(objs)!=1:raise SafetyError('人工目标仅影响当前一项')
            target=self.get(b['target_id']) if manual else None
            if target and not target.get('directory'):raise SafetyError('请选择已有目录')
            if manual and not str(b.get('evidence','')).strip():raise SafetyError('请填写本次归属依据')
            internal=[];external=[];decisions={}
            for obj in objs:
                if obj.get('policy_hash') and obj['policy_hash']!=app.policy.hash:raise SafetyError('规则已变化，请重新扫描')
                source=inboxes.resolve(app.root,app.state,obj['path'])
                if obj.get('scan_fingerprint') and scanning.metadata(source)['fingerprint']!=obj['scan_fingerprint']:raise SafetyError('文件已变化，请重新扫描')
                m=manifest(source);fingerprint=digest(m)
                if obj.get('fingerprint') and obj['fingerprint']!=fingerprint:raise SafetyError('文件版本已变化，请重新扫描或读取详情')
                if not obj.get('scan_fingerprint') and not obj.get('fingerprint'):raise SafetyError('请先扫描或读取详情')
                obj['fingerprint']=fingerprint
                expected='review' if waiting else 'move'
                if not manual and obj.get('action',{}).get('decision')!=expected:raise SafetyError('包含尚未确定目标或不可执行的资料')
                if obj['path'].startswith(inboxes.PREFIX):
                    if waiting:raise SafetyError('外部资料可直接保留原位待确认，无需复制到待确认目录')
                    external.append(obj)
                else:
                    internal.append(obj)
                    if manual or obj.get('action',{}).get('ai_classified'):
                        decisions[obj['path']]={'target_parent':target['path'] if manual else obj['action']['target_parent'],
                            'manifest_hash':fingerprint,'evidence':str(b.get('evidence',''))[:2000] if manual else obj['action']['evidence']}
            parts=[]
            if internal:
                if len({digest(o.get('context',{})) for o in internal})>1:raise SafetyError('不同任务上下文的资料请分别预览')
                plan=app.plan(sources=[o['path'] for o in internal],include_root=manual or any(o.get('include_root') for o in internal),
                    include_review=manual or any(o.get('review_id') for o in internal),allow_existing=manual or any(o.get('review_id') for o in internal),
                    decisions=decisions,approve_decisions=bool(decisions),context=internal[0].get('context'))
                expected='review' if waiting else 'move'
                for obj,item in zip(internal,plan['actions']):
                    if item['decision']!=expected:raise SafetyError('计划中有冲突或尚未确定的归属')
                    if item.get('manifest_hash')!=obj['fingerprint']:raise SafetyError('预览期间资料已变化')
                    if not manual and item.get('target')!=obj['action'].get('target'):raise SafetyError('归属建议已变化，请重新扫描')
                    if obj.get('action',{}).get('classification_verified'):item['classification_verified']=True
                    if obj.get('action',{}).get('ai_classified'):
                        item['ai_classified']=True
                        if not manual:item.update(manual=False,rule_id='ai_classification')
                with app.state.db:app.state.db.execute('UPDATE plans SET data=? WHERE id=?',(json.dumps(plan,ensure_ascii=False),plan['id']))
                write_json(app.root/'_整理规则/执行记录'/plan['id']/'plan.json',plan);parts.append(plan)
            if external:parts.append(importing.build(app,external,target_parent=target['path'] if manual else None,evidence=str(b.get('evidence',''))[:2000]))
            actions=[item for part in parts for item in part['actions']]
            if len({item['target'].casefold() for item in actions})!=len(actions):raise SafetyError('多个来源存在同名目标，不能同时归档')
            plan=parts[0] if len(parts)==1 else {'id':uid(),'type':'mixed','created_at':now(),'policy_hash':app.policy.hash,'actions':actions}
            self.previews[plan['id']]={'plan':plan,'parts':parts,'time':time.monotonic(),'used':False,'objects':[dict(obj) for obj in objs]}
            return self.public_plan(plan)
        if action == 'execute':
            preview=self.previews.get(b.get('plan_id'))
            if not preview or preview['used'] or time.monotonic()-preview['time']>900:raise SafetyError('预览已失效，请重新预览')
            plan=preview['plan']
            if plan['policy_hash']!=app.policy.hash:raise SafetyError('规则已变化，请重新预览')
            for obj in preview['objects']:
                if digest(manifest(inboxes.resolve(app.root,app.state,obj['path'])))!=obj['fingerprint']:raise SafetyError('源文件已变化，请重新预览')
                if obj.get('assignment_fingerprint'):
                    row=app.state.db.execute('SELECT a.data FROM items i JOIN assignments a ON a.item_id=i.id WHERE i.path=?',(obj['path'],)).fetchone()
                    if digest(json.loads(row[0]) if row else {})!=obj['assignment_fingerprint']:raise SafetyError('归属已变化，请刷新待办')
            preview['used']=True;results=[]
            for part in preview.get('parts',[plan]):
                external=part.get('type') in ('external_copy','external_import')
                job.update(phase='按来源设置归档并核验' if external else '移动并核验',run_id=part['id'],total=len(part['actions']))
                result=importing.execute(app,part) if external else app.execute(part['id'])
                results.extend(result['results'])
                for action,row in zip(part['actions'],result['results']):
                    if row['status']=='committed' and action.get('ai_classified'):
                        try:
                            target=safe_path(app.root,action['target'])
                            ai_audit.save(app,action['target'],manifest(target),AIManager(app).settings()['model'],'confirm','AI确定归属并已由用户执行核验')
                        except (OSError,SafetyError):pass
            return {'run_id':plan['id'],'results':results}
        if action == 'keep':
            obj = self.get(b['id'])
            context = obj.get('review_context')
            if not context: raise SafetyError('请先查看详情')
            return app.import_review({'schema_version':'1.0', 'policy_hash':app.policy.hash,
                'items': [{**context, 'decision':'confirm', 'reason':'用户选择保留当前归属'}]})
        if action == 'resolve-external':
            obj = self.get(b['id'])
            if not obj.get('review_id') or not obj.get('review_context_hash'):
                raise SafetyError('请选择待办记录')
            return resolve_external(app.root, app.state, obj['review_id'], obj['review_context_hash'])
        if action == 'review-export':
            target = app.state.profile / 'exports' / ('review-' + uid() + '.json')
            app.export_review(target, with_content=b.get('with_content') is True, limit=500)
            return self.download(target)
        if action == 'review-import':
            payload = b.get('payload')
            if not isinstance(payload, dict): raise SafetyError('审核结果必须为JSON对象')
            return app.import_review(payload)
        if action == 'recover': return app.recover()
        if action == 'undo-preview':
            obj = self.get(b['id'])
            row = app.state.db.execute("SELECT data FROM operations WHERE id=? AND status='committed'", (obj.get('operation_id'),)).fetchone()
            if not row: raise SafetyError('只能撤销已完成操作')
            op = json.loads(row[0])
            if op.get('operation_type') in ('external_copy','external_move'):raise SafetyError('外部归档不支持自动反向转移，请在资源管理器中核对处理')
            if safe_path(self.root, op['source']).exists(): raise SafetyError('原位置已占用，无法撤销')
            if manifest(safe_path(self.root, op['target'])) != op['manifest']: raise SafetyError('内容已变化，无法撤销')
            if app.state.get('paused', False): raise SafetyError('执行已暂停，请先恢复')
            ticket = uid()
            self.previews[ticket] = {'operation_id':op['id'], 'time':time.monotonic(), 'used':False, 'policy_hash':app.policy.hash}
            return {'ticket':ticket, 'source':op['target'], 'target':op['source'], 'verified':True}
        if action == 'undo':
            p = self.previews.get(b.get('ticket'))
            if not p or p.get('used') or time.monotonic()-p['time'] > 300 or p.get('policy_hash') != app.policy.hash: raise SafetyError('撤销预览已失效')
            p['used'] = True
            return undo(app.root, app.state, app.policy, p['operation_id'])
        if action == 'pause': return app.pause(b.get('paused') is True)
        if action == 'authorize':
            auth = {'id':uid(), 'policy_hash':app.policy.hash, 'root':str(app.root), 'include_root':b.get('include_root') is True, 'at':now()}
            app.state.set('automation', auth)
            return {'authorized':True, 'started':False}
        if action == 'revoke':
            self.stop.set(); app.state.set('automation', None)
            return {'authorized':False}
        if action == 'watch-start':
            auth = app.state.get('automation')
            if not auth or auth['policy_hash'] != app.policy.hash: raise SafetyError('需先对当前规则持续授权')
            if self.watch and self.watch.is_alive(): raise SafetyError('后台正在运行或停止中，请稍后刷新')
            self.stop = threading.Event(); self.watch_error = None
            self.watch = threading.Thread(target=self.watch_loop, args=(auth, self.stop), daemon=True)
            self.watch.start()
            return {'started':True}
        if action == 'watch-stop':
            self.stop.set()
            return {'stopping':True, 'note':'当前单项处理结束后停止；未执行项不会另起一轮'}
        if action == 'snapshot':
            folder = app.state.profile / 'exports'; folder.mkdir(exist_ok=True)
            database = folder / ('snapshot-' + uid() + '.sqlite3')
            app.snapshot(database)
            archive = database.with_suffix('.zip')
            with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as z:
                z.write(database, database.name)
                meta = database.with_name(database.name + '.metadata.json')
                z.write(meta, meta.name)
            return self.download(archive)
        raise SafetyError('未知操作')

    def watch_loop(self, auth, stop):
        try:
            while not stop.is_set():
                with self.serial, self.app() as app:
                    if stop.is_set() or app.state.get('automation') != auth: break
                    if app.policy.hash != auth['policy_hash']: raise SafetyError('规则变化，后台已停止；请重新授权')
                    if not app.state.get('paused', False):
                        app.recover()
                        plan = app.plan(include_root=auth['include_root'], automatic=True)
                        if plan['actions'] and not stop.is_set(): app.execute(plan['id'])
                stop.wait(60)
        except Exception as exc:
            self.watch_error = str(exc)

    def close(self):
        self.stop.set()
        self.audit_stop.set()
        for event in self.job_stops.values():event.set()
        for event in list(self.search_stops.values()):event.set()
        self.read_pool.shutdown(wait=True)
        if self.watch: self.watch.join()
        self.pool.shutdown(wait=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def send(self, status, data, content_type='application/json; charset=utf-8', extra=None):
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8') if content_type.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(payload)

    def guard(self, mutation=False):
        expected = urlsplit(self.server.desktop.url).netloc
        if self.headers.get('Host') != expected: raise SafetyError('Host不匹配')
        origin = self.headers.get('Origin')
        if origin and origin != self.server.desktop.url: raise SafetyError('跨域请求被拒绝')
        if self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'): raise SafetyError('跨站请求被拒绝')
        if mutation and (origin != self.server.desktop.url or not hmac.compare_digest(self.headers.get('X-Storage-Token', ''), self.server.desktop.token)):
            raise SafetyError('本地会话验证失败，请刷新页面')

    def do_GET(self):
        try:
            self.guard()
            d = self.server.desktop; url = urlsplit(self.path); q = parse_qs(url.query)
            if url.path == '/api/bootstrap': result = {**d.bootstrap(), 'token':d.token}
            elif url.path == '/api/pending': result = d.pending()
            elif url.path == '/api/review-history': result = d.pending(history=True)
            elif url.path == '/api/operations': result = d.operations()
            elif url.path == '/api/inboxes':
                with d.app() as app:result=inboxes.listing(app.root,app.state)
            elif url.path=='/api/rules':
                with d.app() as app:result=RuleManager(app).status()
            elif url.path=='/api/workbench':
                from .workbench import status
                with d.app() as app:result=status(app)
            elif url.path=='/api/rules-samples':result=d.rule_samples(q.get('id',[None])[0],int(q.get('offset',['0'])[0]))
            elif url.path=='/api/overview':
                with d.app() as app:result={'preferences':overview.preferences(app.state),'links':d.graph_links(app),'root':d.graph_node(overview.describe(app.root,''))}
            elif url.path=='/api/overview-children':
                with d.app() as app:result=d.graph_children(app,q.get('id',[None])[0],q.get('files',['false'])[0]=='true',int(q.get('offset',['0'])[0]))
            elif url.path=='/api/overview-detail':
                with d.app() as app:
                    node=overview.check_node(app.root,d.get(q.get('id',[None])[0]));result=d.graph_node(node)
                    if node['kind']=='directory':
                        dirs=files=0
                        with os.scandir(overview.path_at(app.root,node['path'])) as entries:
                            for entry in entries:
                                if entry.is_dir(follow_symlinks=False):dirs+=1
                                else:files+=1
                                if dirs+files>=10000:break
                        result.update(directories=dirs,files=files,count_limited=dirs+files>=10000)
            elif url.path == '/api/activity':result=d.history(int(q.get('offset',['0'])[0]),q.get('query',[''])[0],q.get('status',['all'])[0])
            elif url.path == '/api/directories': result = d.directory(q.get('id', [None])[0],q.get('archived',['false'])[0]=='true')
            elif url.path.startswith('/api/jobs/'):
                result = dict(d.jobs[url.path.rsplit('/', 1)[1]])
                if result.get('run_id') and result['state'] == 'running':
                    with d.app() as app:
                        result['completed'] = app.state.db.execute("SELECT COUNT(*) FROM operations WHERE run_id=? AND status='committed'", (result['run_id'],)).fetchone()[0]
            elif url.path.startswith('/download/'):
                target = d.downloads[url.path.rsplit('/', 1)[1]]
                return self.send(200, target.read_bytes(), 'application/octet-stream', {'Content-Disposition':'attachment; filename="' + target.name + '"'})
            elif url.path in ('/', '/app.js', '/app.css','/ai.js','/management.js','/overview.js','/overview.css','/ocr.js','/rules.js','/rules.css','/workbench.js','/workbench.css'):
                name = 'index.html' if url.path == '/' else url.path[1:]
                types = {'index.html':'text/html; charset=utf-8', 'app.js':'text/javascript; charset=utf-8', 'ai.js':'text/javascript; charset=utf-8','management.js':'text/javascript; charset=utf-8', 'app.css':'text/css; charset=utf-8'}
                types.update({'overview.js':'text/javascript; charset=utf-8','overview.css':'text/css; charset=utf-8','ocr.js':'text/javascript; charset=utf-8'})
                types.update({'workbench.js':'text/javascript; charset=utf-8','workbench.css':'text/css; charset=utf-8'})
                types.update({'rules.js':'text/javascript; charset=utf-8','rules.css':'text/css; charset=utf-8'})
                return self.send(200, (STATIC / name).read_bytes(), types[name])
            else: return self.send(404, {'error':'页面不存在'})
            self.send(200, result)
        except (SafetyError, KeyError, ValueError, OSError) as exc: self.send(400, {'error':str(exc)})
        except Exception as exc: self.send(500, {'error':str(exc)})

    def do_POST(self):
        try:
            self.guard(True)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json': raise SafetyError('需使用JSON请求')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4 * 1024 * 1024: raise SafetyError('请求大小无效或超过4MiB')
            self.connection.settimeout(15)
            raw = self.rfile.read(length)
            self._body_read = True
            b = json.loads(raw)
            if not isinstance(b, dict): raise SafetyError('需使用对象请求')
            action = urlsplit(self.path).path.removeprefix('/api/')
            d = self.server.desktop
            if action == 'shutdown':
                d.stop.set()
                self.send(200, {'stopping':True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            elif action=='task-stop':
                event=d.job_stops.get(b.get('id'))
                if not event:raise SafetyError('任务已结束或不属于当前服务')
                event.set()
                if d.jobs.get(b.get('id'),{}).get('action')=='library-audit':d.audit_stop.set()
                result={'stop_requested':True,'safe_boundary':True}
            elif action=='task-resume':
                with d.app() as app:
                    record=app.state.get('job_retries',{}).get(b.get('id'))
                    if not record or record['action'] not in ('scan','library-audit','wb-index'):raise SafetyError('此任务需要重新预览，不能直接继续')
                    if record['policy_hash']!=app.policy.hash:raise SafetyError('规则已变化，请重新发起任务')
                    if any(j.get('state') in ('queued','running') for j in d.jobs.values()):raise SafetyError('请先等待当前服务任务结束')
                    from .tasks import alive
                    if record['status']=='queued' and alive(record.get('owner_pid',0)):raise SafetyError('原任务仍在另一窗口运行，请先停止')
                    data={**record['body'],'_page':record['page'],'confirmed':True}
                result=d.job(record['action'],data)
            elif action=='audit-stop':d.audit_stop.set();result={'stop_requested':True,'current_batch_will_finish':True}
            elif action=='overview-search':result=d.graph_search(b)
            elif action=='overview-stop':
                event=d.search_stops.get(b.get('id'))
                if event:event.set()
                result={'stop_requested':True}
            elif action in ('overview-preferences','overview-link','overview-unlink','overview-open'):
                with d.app() as app:
                    if action=='overview-preferences':result=overview.save_preferences(app.root,app.state,b)
                    elif action=='overview-link':result=overview.save_link(app.root,app.state,d.get(b.get('source_id')),d.get(b.get('target_id')),b)
                    elif action=='overview-unlink':result=overview.delete_link(app.state,b.get('id'))
                    else:
                        node=overview.check_node(app.root,d.get(b.get('id')));target=overview.path_at(app.root,node['path'])
                        os.startfile(str(target if node['kind']=='directory' else target.parent));result={'opened':True}
            elif action=='inbox-pick':
                picker=getattr(d,'folder_picker',None)
                if not picker:raise SafetyError('网页版请粘贴文件夹绝对路径，桌面版可使用选择器')
                result={'path':picker()}
            elif action == 'open':
                if b.get('root') == 'library': target = d.root
                elif b.get('root') == 'inbox': target = safe_path(d.root, '00_收件箱')
                elif b.get('inbox_id'):
                    with d.app() as app:target=inboxes.source_root(d.root,app.state,b['inbox_id'],enabled=False)
                else:
                    with d.app() as app:target=inboxes.resolve(d.root,app.state,d.get(b.get('id'))['path'],enabled=False)
                    if not target.is_dir(): target = target.parent
                if not target.is_dir(): raise SafetyError('目录不存在')
                os.startfile(str(target))
                result = {'opened':True}
            elif action.startswith('wb-') or action in {'rules-save','rules-reset','rules-rebase','rules-validate','rules-simulate','rules-preview','rules-publish','rules-reading','ai-configure','ai-test','ai-clear-key','ai-prepare','ai-run','ai-adopt','scan','detail','preview','execute','keep','resolve-external','review-export','review-import','recover','undo-preview','undo','pause','authorize','revoke','watch-start','watch-stop','snapshot','inbox-add','inbox-toggle','inbox-mode','inbox-remove','library-audit','cleanup-preview','cleanup-execute','ocr-configure','ocr-clear-cache','ocr-test'}:
                result = d.job(action, b)
            else: raise SafetyError('未知操作')
            self.send(200, result)
        except (SafetyError, KeyError, ValueError, OSError) as exc:
            # Drain small rejected bodies so Windows does not reset the socket
            # before the client can receive the rejection response.
            if not getattr(self,'_body_read',False):
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if 0<length<=4096:
                        self.connection.settimeout(.3);self.rfile.read(length)
                except (ValueError,OSError):pass
            self.send(400, {'error':str(exc)})
        except Exception as exc: self.send(500, {'error':str(exc)})


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address=False
    request_queue_size=128
    def server_bind(self):
        import socket
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def make_server(root, profile=None, port=0):
    desktop = Desktop(root, profile)
    try:server = LocalServer(('127.0.0.1', port), Handler)
    except BaseException:desktop.close();raise
    server.daemon_threads = True
    server.desktop = desktop
    desktop.url = 'http://127.0.0.1:' + str(server.server_port)
    return server


def serve(root, profile=None, port=0, open_browser=True):
    try:server = make_server(root, profile, port)
    except OSError as exc:
        if not port:raise
        import urllib.request
        try:
            url='http://127.0.0.1:'+str(port)
            with urllib.request.urlopen(url+'/api/bootstrap',timeout=2) as response:existing=json.load(response)
            if Path(existing['status']['root']).resolve()!=Path(root).resolve():raise SafetyError('该端口被另一个资料库或程序占用，请选择其他端口')
            print('该资料库网页已在运行：'+url+'，版本 '+existing['app_version'],flush=True)
            if open_browser:webbrowser.open(url)
            return
        except (OSError,ValueError,KeyError):raise SafetyError('端口已占用，请检查已有实例或选择其他端口') from exc
    print('PersonalStorage ' + server.desktop.url, flush=True)
    print('关闭此服务窗口将停止界面与后台轮询；浏览器关闭不会停止服务。Ctrl+C退出。', flush=True)
    if open_browser: webbrowser.open(server.desktop.url)
    try: server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt: pass
    finally:
        server.server_close(); server.desktop.close()
