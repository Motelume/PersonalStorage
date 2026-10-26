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
        self.downloads = {}
        self.ai_packets = {}
        self.credential_vault = None
        self.ai_provider_factory = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='storage-ui')
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

    def job(self, action, body):
        ident = uid()
        self.jobs[ident] = {'id': ident, 'state': 'queued', 'phase': '等待处理', 'at': now()}
        def work():
            job = self.jobs[ident]
            job.update(state='running', phase='核对与处理')
            try:
                with self.serial, self.app() as app:
                    result = self.perform(app, action, body, job)
                job.update(state='done', phase='已完成', result=result)
            except Exception as exc:
                job.update(state='error', phase='未完成', error=str(exc))
        self.pool.submit(work)
        return {'job_id': ident}

    def bootstrap(self):
        with self.app() as app:
            s = app.status()
            s.update(watching=bool(self.watch and self.watch.is_alive()), watch_error=self.watch_error)
            auth = app.state.get('automation')
            ai = AIManager(app, vault=self.credential_vault).status()
            return {'status': s, 'policy': app.policy.data, 'policy_hash': app.policy.hash, 'ai':ai,'ui_mode':'desktop' if getattr(self,'native_mode',False) else 'web',
                    'authorization_current': bool(auth and auth['policy_hash'] == app.policy.hash),
                    'authorization_scope_root': bool(auth and auth.get('include_root')),
                    'model_enabled': ai['settings']['enabled'] and ai['configured']}

    def pending(self, history=False):
        with self.app() as app:
            out = reports.resolved_reviews(app.state) if history else reports.pending(app.state)
            for r in out:
                context = review_context({'id': r['review_id'], 'path': r['path'],
                    'status': r['status'], 'data': json.dumps(r['details'], ensure_ascii=False)})
                r['id'] = self.object(r['path'], review_id=r['review_id'], review_context_hash=context)
                try:
                    r['available'] = safe_path(self.root, r['path']).exists()
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

    def directory(self, ident=None):
        with self.app() as app:
            parent = self.get(ident)['path'] if ident else None
            paths = [p for p in safe_path(self.root, parent).iterdir()] if parent else [self.root / p for p in sorted(app.policy.categories - {'00_收件箱', '09_移动硬盘管理'})]
            out = []
            for p in sorted(paths, key=lambda x: x.name.casefold()):
                if not p.is_dir() or linked(p):
                    continue
                rel = p.relative_to(self.root).as_posix()
                try:
                    app.policy.target(rel, '__target_check__')
                except SafetyError:
                    continue
                out.append({'id': self.object(rel, directory=True), 'name': p.name, 'path': rel})
            return {'path': parent or '', 'items': out}

    def download(self, path):
        ident = uid()
        self.downloads[ident] = Path(path)
        return {'download': '/download/' + ident, 'filename': Path(path).name}

    def public_plan(self, plan):
        return {'id': plan['id'], 'created_at': plan['created_at'], 'actions': [
            {k: v for k, v in a.items() if k != 'manifest'} for a in plan['actions']]}

    def perform(self, app, action, b, job):
        if action.startswith('ai-'):
            manager=AIManager(app, vault=self.credential_vault, provider_factory=self.ai_provider_factory)
            if action=='ai-configure':return manager.configure(b)
            if action=='ai-test':return manager.test()
            if action=='ai-clear-key':return manager.clear_key()
            if action=='ai-prepare':
                ids=b.get('ids')
                if not isinstance(ids,list) or not ids or len(ids)>10:raise SafetyError('请选择1至10项资料')
                packet=manager.prepare([self.get(i) for i in ids])
                self.ai_packets[packet['id']]=packet
                return {'ticket':packet['id'],'model':packet['config']['model'],'with_content':packet['config']['with_content'],
                    'target_count':len(packet['request']['candidate_targets']),
                    'items':[{'name':d['name'],'source':d['source'],'excerpt':d['text'],'members':d['members'],'warnings':d['warnings']} for d in packet['request']['documents']]}
            if action=='ai-run':
                packet=self.ai_packets.pop(b.get('ticket'),None)
                if not packet:raise SafetyError('AI发送预览已失效，请重新选择')
                job['phase']='DeepSeek正在审核，等待结构化结果'
                return manager.run(packet)
            if action=='ai-adopt':
                obj=self.get(b['id'])
                row=app.state.db.execute('SELECT r.*,i.path FROM reviews r JOIN items i ON i.id=r.item_id WHERE r.id=?',(obj.get('review_id'),)).fetchone()
                if not row or row['status']!='correction_required' or review_context(row)!=obj.get('review_context_hash'):raise SafetyError('建议已变化，请刷新待办')
                data=json.loads(row['data']);result=data.get('result',{})
                if result.get('decision')!='suggest_reclassify':raise SafetyError('没有可采用的归属建议')
                if digest(manifest(safe_path(app.root,obj['path'])))!=data['version']:raise SafetyError('资料已变化，请重新审核')
                parent=result['target_parent']
                if not safe_path(app.root,parent).is_dir():raise SafetyError('建议目录已不存在')
                self.perform(app,'detail',b,job)
                return self.perform(app,'preview',{'ids':[b['id']],'target_id':self.object(parent,directory=True),
                    'evidence':'用户采用AI建议：'+result.get('reason','')},job)
        if action == 'scan':
            job['phase'] = '读取收件箱并生成建议'
            plan = app.plan(include_root=b.get('include_root') is True)
            rows = []
            for a in plan['actions']:
                ident = self.object(a['source'], fingerprint=a.get('manifest_hash'), action=a,
                                    policy_hash=app.policy.hash, include_root=plan['scope']['include_root'])
                rows.append({'id': ident, **{k: v for k, v in a.items() if k not in ('id', 'manifest')},
                             'kind': a.get('manifest', {}).get('kind'),
                             'file_count': len(a.get('manifest', {}).get('files', []))})
            return {'rows': rows, 'scanned_at': now()}
        if action == 'detail':
            obj = self.get(b['id']); path = safe_path(self.root, obj['path'])
            m = manifest(path); obj['fingerprint'] = digest(m)
            result = {'path': obj['path'], 'absolute_path': str(path), 'fingerprint': digest(m),
                      'features': extract(path, limit=8000), 'file_count': len(m['files']),
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
            ids = b.get('ids', [])
            if not isinstance(ids, list) or not ids or len(ids) > 500: raise SafetyError('请选择1至500项')
            objs = [self.get(i) for i in ids]
            manual = bool(b.get('target_id'))
            waiting = b.get('waiting') is True
            if manual and len(objs) != 1: raise SafetyError('人工目标仅影响当前一项')
            target = self.get(b['target_id']) if manual else None
            if target and not target.get('directory'): raise SafetyError('请选择已有目录')
            decisions = {}
            for obj in objs:
                if obj.get('policy_hash') and obj['policy_hash'] != app.policy.hash: raise SafetyError('规则已变化，请重新扫描')
                if not obj.get('fingerprint') or digest(manifest(safe_path(self.root, obj['path']))) != obj['fingerprint']:
                    raise SafetyError('文件已变化，请重新扫描或查看详情')
                if not manual:
                    expected = 'review' if waiting else 'move'
                    if obj.get('action', {}).get('decision') != expected: raise SafetyError('包含不可执行项')
                else:
                    if not str(b.get('evidence', '')).strip(): raise SafetyError('请填写本次归属依据')
                    decisions[obj['path']] = {'target_parent': target['path'], 'manifest_hash': obj['fingerprint'], 'evidence': str(b['evidence'])[:2000]}
            plan = app.plan(sources=[o['path'] for o in objs], include_root=manual or any(o.get('include_root') for o in objs),
                include_review=manual, allow_existing=manual, decisions=decisions, approve_decisions=manual)
            expected = 'review' if waiting else 'move'
            if any(a['decision'] != expected for a in plan['actions']):
                raise SafetyError('计划中有冲突或不可执行项，请重新扫描或选择目标')
            for obj, a in zip(objs, plan['actions']):
                if a.get('manifest_hash') != obj['fingerprint']: raise SafetyError('文件在预览时变化，请重新扫描')
            self.previews[plan['id']] = {'plan': plan, 'time': time.monotonic(), 'used': False, 'objects': [dict(o) for o in objs]}
            return self.public_plan(plan)
        if action == 'execute':
            preview = self.previews.get(b.get('plan_id'))
            if not preview or preview['used'] or time.monotonic() - preview['time'] > 900: raise SafetyError('预览已失效，请重新预览')
            plan = preview['plan']
            if plan['policy_hash'] != app.policy.hash: raise SafetyError('规则已变化，请重新预览')
            for obj, a in zip(preview['objects'], plan['actions']):
                if digest(manifest(safe_path(self.root, a['source']))) != a['manifest_hash']: raise SafetyError('源文件已变化，请重新预览')
                if obj.get('assignment_fingerprint'):
                    row = app.state.db.execute('SELECT a.data FROM items i JOIN assignments a ON a.item_id=i.id WHERE i.path=?', (obj['path'],)).fetchone()
                    if digest(json.loads(row[0]) if row else {}) != obj['assignment_fingerprint']: raise SafetyError('归属已变化，请刷新待办')
            preview['used'] = True
            job.update(phase='移动并核验（逐项记录）', run_id=plan['id'], total=len(plan['actions']))
            return app.execute(plan['id'])
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
            elif url.path == '/api/directories': result = d.directory(q.get('id', [None])[0])
            elif url.path.startswith('/api/jobs/'):
                result = dict(d.jobs[url.path.rsplit('/', 1)[1]])
                if result.get('run_id') and result['state'] == 'running':
                    with d.app() as app:
                        result['completed'] = app.state.db.execute("SELECT COUNT(*) FROM operations WHERE run_id=? AND status='committed'", (result['run_id'],)).fetchone()[0]
            elif url.path.startswith('/download/'):
                target = d.downloads[url.path.rsplit('/', 1)[1]]
                return self.send(200, target.read_bytes(), 'application/octet-stream', {'Content-Disposition':'attachment; filename="' + target.name + '"'})
            elif url.path in ('/', '/app.js', '/app.css','/ai.js'):
                name = 'index.html' if url.path == '/' else url.path[1:]
                types = {'index.html':'text/html; charset=utf-8', 'app.js':'text/javascript; charset=utf-8', 'ai.js':'text/javascript; charset=utf-8', 'app.css':'text/css; charset=utf-8'}
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
            elif action == 'open':
                if b.get('root') == 'library': target = d.root
                elif b.get('root') == 'inbox': target = safe_path(d.root, '00_收件箱')
                else:
                    target = safe_path(d.root, d.get(b.get('id'))['path'])
                    if not target.is_dir(): target = target.parent
                if not target.is_dir(): raise SafetyError('目录不存在')
                os.startfile(str(target))
                result = {'opened':True}
            elif action in {'ai-configure','ai-test','ai-clear-key','ai-prepare','ai-run','ai-adopt','scan','detail','preview','execute','keep','resolve-external','review-export','review-import','recover','undo-preview','undo','pause','authorize','revoke','watch-start','watch-stop','snapshot'}:
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


def make_server(root, profile=None, port=0):
    desktop = Desktop(root, profile)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.desktop = desktop
    desktop.url = 'http://127.0.0.1:' + str(server.server_port)
    return server


def serve(root, profile=None, port=0, open_browser=True):
    server = make_server(root, profile, port)
    print('PersonalStorage ' + server.desktop.url, flush=True)
    print('关闭此服务窗口将停止界面与后台轮询；浏览器关闭不会停止服务。Ctrl+C退出。', flush=True)
    if open_browser: webbrowser.open(server.desktop.url)
    try: server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt: pass
    finally:
        server.server_close(); server.desktop.close()
