import json
import threading
import unittest
from unittest.mock import patch
from personalstorage import overview
from personalstorage.common import SafetyError,manifest
import test_web as fixtures

class OverviewTests(unittest.TestCase):
    setUp=fixtures.WebTests.setUp
    tearDown=fixtures.WebTests.tearDown
    request=fixtures.WebTests.request
    job=fixtures.WebTests.job

    def roots(self):return self.request('overview-children')['nodes']
    def folder(self,name):return next(n for n in self.roots() if n['name']==name)

    def test_lazy_metadata_navigation_does_not_hash_or_parse_documents(self):
        folder=self.root/'01_课程学习/测试课程';(folder/'unread.txt').write_text('private body')
        with patch('personalstorage.common.manifest',side_effect=AssertionError('must not hash')):
            roots=self.roots();self.assertTrue(all(n['kind']=='directory' for n in roots))
            course=self.folder('01_课程学习');children=self.request('overview-children?id='+course['id'])['nodes']
            self.assertEqual([n['name'] for n in children],['测试课程'])
        with self.server.desktop.app() as app:self.assertEqual(app.state.db.execute('SELECT COUNT(*) FROM items').fetchone()[0],0)

    def test_paging_and_files_switch(self):
        folder=self.root/'01_课程学习'
        for i in range(210):(folder/f'folder-{i:03}').mkdir()
        (folder/'a.txt').write_text('text');node=self.folder(folder.name)
        first=self.request('overview-children?id='+node['id']);self.assertEqual(len(first['nodes']),200);self.assertEqual(first['next_offset'],200)
        second=self.request('overview-children?id='+node['id']+'&offset=200&files=true');self.assertIn('a.txt',[n['name'] for n in second['nodes']])

    def test_invalid_object_and_traversal_are_rejected(self):
        from urllib.error import HTTPError
        for endpoint in ('overview-children?id=../outside','overview-detail?id=unknown'):
            with self.assertRaises(HTTPError):self.request(endpoint)
        with self.assertRaises(HTTPError):self.request('overview-preferences',{'positions':{'../outside':{'x':0,'y':0}}})

    def test_preferences_validation_and_persistence(self):
        prefs={'mode':'tree','motion':False,'positions':{'01_课程学习':{'x':20,'y':-9}},'camera':{'x':10,'y':20,'zoom':.8}}
        self.request('overview-preferences',prefs);saved=self.request('overview')['preferences']
        for key,value in prefs.items():self.assertEqual(saved[key],value)
        with self.server.desktop.app() as app:
            with self.assertRaises(SafetyError):overview.save_preferences(self.root,app.state,{'positions':{'a':{'x':float('inf'),'y':0}}})
            with self.assertRaises(SafetyError):overview.save_preferences(self.root,app.state,{'motion':'yes'})

    def test_manual_link_crud_never_changes_originals(self):
        a=self.folder('00_收件箱');b=self.folder('01_课程学习');before=[manifest(self.root/a['path']),manifest(self.root/b['path'])]
        created=self.request('overview-link',{'source_id':a['id'],'target_id':b['id'],'label':'参考资料','note':'手动说明'})
        self.assertTrue(created['saved']);link=self.request('overview')['links'][0];self.assertEqual(link['source']['path'],a['path'])
        self.request('overview-link',{'link_id':created['id'],'source_id':a['id'],'target_id':b['id'],'label':'相关任务'})
        self.assertEqual(self.request('overview')['links'][0]['label'],'相关任务')
        self.request('overview-unlink',{'id':created['id']});self.assertEqual(self.request('overview')['links'],[])
        self.assertEqual([manifest(self.root/a['path']),manifest(self.root/b['path'])],before)

    def test_replaced_endpoint_requires_explicit_rebinding(self):
        a=self.folder('00_收件箱');b=self.folder('01_课程学习')
        self.request('overview-link',{'source_id':a['id'],'target_id':b['id'],'label':'参考'})
        original=self.root/a['path'];original.rename(self.root/'old-inbox');original.mkdir()
        link=self.request('overview')['links'][0];self.assertFalse(link['source']['available'])
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError):self.request('overview-link',{'source_id':a['id'],'target_id':b['id'],'label':'不能误绑定'})

    def test_search_works_while_file_queue_is_occupied(self):
        release=threading.Event();started=threading.Event()
        def blocked():started.set();release.wait(10)
        future=self.server.desktop.pool.submit(blocked);started.wait(3)
        try:
            self.assertTrue(self.roots())
            result=self.job('overview-search',{'query':'测试课程','files':True})
            self.assertEqual(result['state'],'done');self.assertTrue(result['result']['nodes']);self.assertFalse(future.done())
        finally:release.set();future.result(3)

    def test_cancel_search_without_content_or_ai(self):
        stop=threading.Event();stop.set();result=overview.search(self.root,'课程',True,stop)
        self.assertTrue(result['stopped']);self.assertEqual(result['scanned'],0)
        result=self.job('overview-search',{'query':'课程','files':False})['result']
        self.assertTrue(all(n['kind']=='directory' for n in result['nodes']))

    def test_open_file_only_opens_its_parent_directory(self):
        folder=self.root/'00_收件箱';(folder/'program.exe').write_bytes(b'not executable')
        parent=self.folder(folder.name);node=self.request('overview-children?id='+parent['id']+'&files=true')['nodes'][0]
        with patch('personalstorage.web.os.startfile') as opened:self.request('overview-open',{'id':node['id']})
        self.assertTrue(folder.samefile(opened.call_args.args[0]))

    def test_self_link_rejected(self):
        from urllib.error import HTTPError
        node=self.folder('00_收件箱')
        with self.assertRaises(HTTPError):self.request('overview-link',{'source_id':node['id'],'target_id':node['id'],'label':'自身'})
