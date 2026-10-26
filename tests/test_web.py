import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from personalstorage.web import make_server
from personalstorage.common import write_json, manifest
from personalstorage import reports
from personalstorage.credentials import CredentialVault
from test_core import config


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base/'library'
        (self.root/'00_收件箱').mkdir(parents=True)
        (self.root/'01_课程学习/测试课程').mkdir(parents=True)
        write_json(self.root/'_整理规则/分类配置.json', config())
        self.server = make_server(self.root, self.base/'profile')
        self.server.desktop.credential_vault = CredentialVault(self.base/'test-credentials.sqlite3')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = self.server.desktop.url
        self.token = self.request('bootstrap')['token']

    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()
        self.server.desktop.close(); self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        hdr = {'Content-Type':'application/json','Origin':self.url,'X-Storage-Token':getattr(self,'token','')}
        hdr.update(headers or {})
        try:
            with urlopen(Request(self.url+'/api/'+path, data=body, headers=hdr), timeout=15) as r:
                return json.load(r)
        except HTTPError as exc:
            exc.close()
            raise

    def job(self, action, body=None):
        ident = self.request(action,body or {})['job_id']
        for _ in range(200):
            j = self.request('jobs/'+ident)
            if j['state'] in ('done','error'): return j
            time.sleep(.03)
        self.fail('job timeout')

    def file(self, name, text='course'):
        p=self.root/'00_收件箱'/name;p.write_text(text,encoding='utf-8');return p

    def test_launch_does_not_authorize_or_start(self):
        b=self.request('bootstrap')
        self.assertFalse(b['status']['watching']);self.assertFalse(b['authorization_current'])
        self.assertEqual(Path(b['status']['profile']), self.base/'profile')

    def test_origin_token_host_and_object_scope(self):
        for headers in [{'Origin':'https://evil.example'},{'X-Storage-Token':'wrong'},{'Host':'evil.example'}]:
            with self.assertRaises(HTTPError): self.request('scan',{},headers)
        self.assertEqual(self.job('detail',{'id':'../outside'})['state'],'error')
        with self.assertRaises(HTTPError): self.request('open',{'id':'C:/Windows'})
        self.assertEqual(self.job('watch-start')['state'],'error')

    def test_selected_only_and_undo(self):
        a=self.file('a.txt','unknown a');b=self.file('b.txt','unknown b')
        rows=self.job('scan')['result']['rows']
        first=next(r for r in rows if r['source'].endswith('a.txt'))
        dirs=self.request('directories')['items'];top=next(x for x in dirs if x['name']=='01_课程学习')
        dest=self.request('directories?id='+top['id'])['items'][0]
        preview=self.job('preview',{'ids':[first['id']],'target_id':dest['id'],'evidence':'explicit course material'})
        self.assertEqual(preview['state'],'done',preview)
        result=self.job('execute',{'plan_id':preview['result']['id']})
        self.assertEqual(result['result']['results'][0]['status'],'committed')
        self.assertTrue(b.exists());self.assertFalse(a.exists())
        operation=self.request('operations')[0]
        pre=self.job('undo-preview',{'id':operation['id_handle']})['result']
        self.assertTrue((self.root/'01_课程学习/测试课程/a.txt').exists())
        self.assertEqual(self.job('undo',{'ticket':pre['ticket']})['state'],'done')
        self.assertTrue(a.exists());self.assertTrue(b.exists())

    def test_scan_not_allowed_to_execute_waiting_rows(self):
        self.file('unknown.txt')
        r=self.job('scan')['result']['rows'][0]
        self.assertEqual(self.job('preview',{'ids':[r['id']]})['state'],'error')
        p=self.job('preview',{'ids':[r['id']],'waiting':True})
        self.assertEqual(p['state'],'done')
        self.assertEqual(self.job('execute',{'plan_id':p['result']['id']})['state'],'done')
        self.assertEqual(len(self.request('pending')),1)

    def test_stale_plan_policy_and_content(self):
        p=self.file('note.txt')
        row=self.job('scan')['result']['rows'][0]
        preview=self.job('preview',{'ids':[row['id']]})['result']
        p.write_text('changed')
        self.assertEqual(self.job('execute',{'plan_id':preview['id']})['state'],'error')
        self.assertTrue(p.exists())

    def test_export_import_snapshot_are_downloads(self):
        result=self.job('review-export')['result']
        with urlopen(self.url+result['download']) as r: self.assertEqual(json.load(r)['schema_version'],'1.0')
        snap=self.job('snapshot')['result']
        with urlopen(self.url+snap['download']) as r: self.assertEqual(r.read(2),b'PK')
        self.assertEqual(self.job('review-import',{'payload':{'schema_version':'bad'}})['state'],'error')

    def test_unsafe_text_is_returned_as_data(self):
        self.file('x.txt','<script>alert(1)</script>')
        row=self.job('scan')['result']['rows'][0]
        detail=self.job('detail',{'id':row['id']})['result']
        self.assertIn('<script>',detail['features']['text'])
        with urlopen(self.url) as r:
            self.assertIn("script-src 'self'",r.headers['Content-Security-Policy'])

    def test_authorization_is_separate_from_start(self):
        self.assertEqual(self.job('authorize')['state'],'done')
        self.assertTrue(self.request('bootstrap')['authorization_current'])
        self.assertFalse(self.request('bootstrap')['status']['watching'])
        self.assertEqual(self.job('watch-start')['state'],'done')
        self.assertTrue(self.request('bootstrap')['status']['watching'])
        self.job('watch-stop');self.server.desktop.watch.join(timeout=5)
        self.assertFalse(self.request('bootstrap')['status']['watching'])

    def test_missing_review_and_operation_without_target(self):
        self.file('unknown.txt')
        row=self.job('scan')['result']['rows'][0]
        p=self.job('preview',{'ids':[row['id']],'waiting':True})['result']
        self.job('execute',{'plan_id':p['id']})
        (self.root/'00_收件箱/待确认/unknown.txt').unlink()
        item=self.request('pending')[0]
        self.assertFalse(item['available']);self.assertEqual(item['status'],'waiting_content')
        with self.server.desktop.app() as app:
            app.state.op('skip-test','skip',{'source':'00_收件箱/missing.txt','run_id':'test'})
        self.assertEqual(self.request('operations')[0]['status'],'skip')

    def externally_moved_review(self):
        self.file('unknown.txt','user handled material')
        row=self.job('scan')['result']['rows'][0]
        plan=self.job('preview',{'ids':[row['id']],'waiting':True})['result']
        self.job('execute',{'plan_id':plan['id']})
        source=self.root/'00_收件箱/待确认/unknown.txt'
        destination=self.root/'01_课程学习/测试课程/unknown.txt'
        source.rename(destination)
        return self.request('pending')[0], destination

    def test_close_external_keeps_history_files_and_assignment(self):
        item, destination=self.externally_moved_review()
        before=manifest(destination)
        with self.server.desktop.app() as app:
            assignment=app.state.db.execute('SELECT * FROM assignments').fetchall()[0]['data']
            operation_count=app.state.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
        result=self.job('resolve-external',{'id':item['id']})
        self.assertEqual(result['state'],'done',result)
        self.assertFalse(result['result']['files_touched'])
        self.assertEqual(self.request('pending'),[])
        history=self.request('review-history')
        self.assertEqual(len(history),1)
        self.assertEqual(history[0]['status'],'resolved_external')
        self.assertEqual(history[0]['details']['resolution']['source'],item['path'])
        self.assertFalse(history[0]['details']['resolution']['new_location_verified'])
        self.assertEqual(manifest(destination),before)
        self.assertEqual(self.job('resolve-external',{'id':item['id']})['state'],'done')
        with self.server.desktop.app() as app:
            self.assertEqual(app.state.db.execute('SELECT data FROM assignments').fetchone()[0],assignment)
            self.assertEqual(app.state.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0],operation_count)
            self.assertEqual(app.state.db.execute("SELECT COUNT(*) FROM events WHERE status='review_resolved_external'").fetchone()[0],1)
        exported=self.job('review-export')['result']
        with urlopen(self.url+exported['download']) as r:self.assertEqual(json.load(r)['items'],[])
        with self.server.desktop.app() as app:
            self.assertEqual(app.pending(),[])
            self.assertEqual(len(reports.resolved_reviews(app.state)),1)

    def test_close_external_rejects_reappeared_file(self):
        item,destination=self.externally_moved_review()
        source=self.root/item['path']; source.write_text('new file at old path')
        result=self.job('resolve-external',{'id':item['id']})
        self.assertEqual(result['state'],'error')
        self.assertIn('原路径当前存在文件',result['error'])
        self.assertEqual(len(self.request('pending')),1)
        self.assertEqual(self.request('review-history'),[])
        self.assertEqual(source.read_text(),'new file at old path')
        self.assertTrue(destination.exists())

    def test_close_external_rejects_changed_review_or_nonreview_handle(self):
        item,_=self.externally_moved_review()
        with self.server.desktop.app() as app:
            with app.state.db:
                app.state.db.execute("UPDATE reviews SET status='waiting_content' WHERE id=?",(item['review_id'],))
        self.assertEqual(self.job('resolve-external',{'id':item['id']})['state'],'error')
        fresh=self.request('pending')[0]
        self.assertEqual(self.job('resolve-external',{'id':fresh['id']})['state'],'done')
        directory=self.request('directories')['items'][0]
        self.assertEqual(self.job('resolve-external',{'id':directory['id']})['state'],'error')

    def test_ai_preview_run_and_adopt_safe_plan(self):
        from test_ai import FakeProvider
        vault=self.server.desktop.credential_vault
        vault.save('sk-'+'test-only-credential'*2)
        provider=FakeProvider()
        self.server.desktop.ai_provider_factory=lambda *a,**k:provider
        configured=self.job('ai-configure',{'enabled':True,'max_items':1})
        self.assertEqual(configured['state'],'done')
        self.file('unknown.txt','example learning notes')
        self.file('other.txt','keep this in inbox')
        rows=self.job('scan')['result']['rows']
        row=next(r for r in rows if r['source'].endswith('unknown.txt'))
        prepared=self.job('ai-prepare',{'ids':[row['id']]})['result']
        self.assertEqual(provider.calls,0)
        self.assertIn('example learning notes',prepared['items'][0]['excerpt'])
        reviewed=self.job('ai-run',{'ticket':prepared['ticket']})
        self.assertEqual(reviewed['state'],'done',reviewed)
        self.assertTrue((self.root/'00_收件箱/unknown.txt').exists())
        pending=self.request('pending')[0]
        plan=self.job('ai-adopt',{'id':pending['id']})
        self.assertEqual(plan['state'],'done',plan)
        self.assertEqual(len(plan['result']['actions']),1)
        self.assertTrue((self.root/'00_收件箱/unknown.txt').exists())
        self.assertEqual(self.job('execute',{'plan_id':plan['result']['id']})['state'],'done')
        self.assertTrue((self.root/'01_课程学习/测试课程/unknown.txt').exists())
        self.assertTrue((self.root/'00_收件箱/other.txt').exists())
        public=json.dumps(self.request('bootstrap'))
        self.assertNotIn(vault.read(),public)

    def test_ai_configuration_and_connection_stay_local_and_safe(self):
        from test_ai import FakeProvider
        provider=FakeProvider();self.server.desktop.ai_provider_factory=lambda *a,**k:provider
        key='sk-'+'test-only-credential'*2
        self.assertEqual(self.job('ai-configure',{'enabled':True,'api_key':key})['state'],'done')
        self.assertEqual(self.job('ai-test')['result']['connected'],True)
        self.assertNotIn(key,json.dumps(self.request('bootstrap')))
        self.assertEqual(self.job('ai-configure',{'model':'unknown'})['state'],'error')
        self.assertEqual(self.job('ai-clear-key')['state'],'done')
        self.assertFalse(self.request('bootstrap')['model_enabled'])
