import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from personalstorage.common import write_json,manifest,SafetyError
from personalstorage.service import Application
from personalstorage.web import make_server
from personalstorage import renaming,searching
from personalstorage.workbench import perform
from test_core import config


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'library';(self.root/'00_收件箱').mkdir(parents=True);(self.root/'01_课程学习/测试课程').mkdir(parents=True)
        write_json(self.root/'_整理规则/分类配置.json',config());self.app=Application(self.root,self.base/'state')
    def tearDown(self):self.app.close();self.tmp.cleanup()
    def file(self,name='新建文本文档.txt',text='# 控制理论课堂笔记\ncourse references'):
        p=self.root/'00_收件箱'/name;p.write_text(text,encoding='utf-8');return p
    def test_names_and_conflicts_preserve_meaning(self):
        p=self.file('控制理论复习.txt');self.assertFalse(renaming.suggest(self.app,p.relative_to(self.root).as_posix())['eligible'])
        p=self.file();s=renaming.suggest(self.app,'00_收件箱/'+p.name);self.assertEqual(s['suggested'],'控制理论课堂笔记.txt')
        for name in ('../escape.txt','CON.txt','new.pdf','new.txt '):
            with self.assertRaises(SafetyError):renaming.preview(self.app,'00_收件箱/'+p.name,name,'evidence')
        self.file('existing.txt')
        with self.assertRaises(SafetyError):renaming.preview(self.app,'00_收件箱/'+p.name,'existing.txt','evidence')
    def test_wrapped_notice_title_preserves_both_lines(self):
        p=self.file(text='关于开展读书活动\n红色教育主题通知\n活动说明');result=renaming.suggest(self.app,'00_收件箱/'+p.name)
        self.assertEqual(result['suggested'],'关于开展读书活动红色教育主题通知.txt')
    def test_rename_recovery_undo_and_search(self):
        p=self.file();original=p.read_bytes();before=manifest(p);self.app.state.observe('00_收件箱/'+p.name,'file',before);searching.build(self.app)
        plan=renaming.preview(self.app,'00_收件箱/'+p.name,'控制理论笔记.txt','title')
        with patch('personalstorage.renaming.finish',side_effect=RuntimeError('crash after rename')):
            with self.assertRaises(RuntimeError):renaming.apply(self.app,plan)
        self.assertTrue(self.app.state.get('file_rename_journal'));renaming.recover(self.app)
        self.assertFalse(p.exists());target=self.root/plan['target'];self.assertEqual(target.read_bytes(),original)
        self.assertEqual(target.stat().st_mtime_ns,before['files'][0]['mtime_ns']);self.assertIsNotNone(self.app.state.item(plan['target']))
        db=searching.database(self.app.state)
        try:self.assertEqual(db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],0)
        finally:db.close()
        reverse=renaming.undo_preview(self.app,plan['id']);renaming.apply(self.app,reverse);self.assertTrue(p.exists());self.assertEqual(manifest(p),before)
    def test_rename_source_changed_and_clear_name_blocked(self):
        p=self.file();plan=renaming.preview(self.app,'00_收件箱/'+p.name,'new.txt','title');p.write_text('changed')
        with self.assertRaises(SafetyError):renaming.apply(self.app,plan)
        self.file('课程笔记.txt')
        with self.assertRaises(SafetyError):renaming.preview(self.app,'00_收件箱/课程笔记.txt','new.txt','guess')
    def test_batch_selection_subset_and_same_name(self):
        server=make_server(self.root,self.base/'desktop');desktop=server.desktop
        try:
            p=self.file('one.txt');q=self.file('two.txt');untouched=self.file('three.txt');ids=[desktop.object('00_收件箱/'+x.name,action={'decision':'review'}) for x in (p,q)]
            target=desktop.object('01_课程学习/测试课程',directory=True)
            with desktop.app() as app:
                result=perform(desktop,app,'wb-batch-preview',{'ids':ids,'target_id':target,'evidence':'explicit user batch context'},{});self.assertEqual(len(result['plans']),2)
                done=perform(desktop,app,'wb-batch-execute',{'ticket':result['ticket'],'plan_ids':[result['plans'][0]['id']]},{})
                self.assertEqual(done['results'][0]['status'],'committed');self.assertFalse(p.exists());self.assertTrue(q.exists());self.assertTrue(untouched.exists())
                with self.assertRaises(SafetyError):perform(desktop,app,'wb-batch-execute',{'ticket':result['ticket'],'plan_ids':[result['plans'][1]['id']]},{})
        finally:server.server_close();desktop.close()
    def test_ai_rename_response_is_bounded(self):
        p=self.file();packet={'source':'00_收件箱/'+p.name,'manifest':manifest(p)}
        with self.assertRaises(SafetyError):renaming.validate_answer(self.app,packet,{'keep':False,'name':'../../outside.txt','reason':'text'})
        self.assertEqual(renaming.validate_answer(self.app,packet,{'keep':True})['suggested'],p.name)
    def test_batch_ai_shared_notes_split_and_adopt(self):
        from test_ai import FakeVault,FakeProvider
        from personalstorage.ai import AIManager
        server=make_server(self.root,self.base/'desktop');d=server.desktop;provider=FakeProvider();d.credential_vault=FakeVault();d.ai_provider_factory=lambda *a,**k:provider
        try:
            p=self.file('one.txt');q=self.file('two.txt');ids=[d.object('00_收件箱/'+x.name,action={'decision':'review'}) for x in (p,q)]
            with d.app() as app:
                AIManager(app,d.credential_vault,d.ai_provider_factory).configure({'enabled':True,'max_items':1})
                prepared=perform(d,app,'wb-batch-ai-preview',{'ids':ids,'common':'同一课程实验材料','notes':{ids[1]:'第二份是补充资料'}},{})
                self.assertEqual(prepared['batches'],2);self.assertEqual(provider.calls,0);self.assertIn('补充资料',prepared['documents'][1]['user_context'])
                result=perform(d,app,'wb-batch-ai-run',{'ticket':prepared['ticket']},{});self.assertEqual(provider.calls,2);self.assertTrue(p.exists() and q.exists());self.assertEqual(len(result['batches']),2)
                pending=d.pending();plans=perform(d,app,'wb-batch-adopt',{'ids':[r['id'] for r in pending]},{});self.assertEqual(len(plans['plans']),2)
                self.assertTrue(p.exists() and q.exists())
        finally:server.server_close();d.close()
    def test_ai_rename_cost_accounting_and_stale_input(self):
        from test_ai import FakeVault
        from personalstorage.ai import AIManager
        from personalstorage.assistance import run
        class Provider:
            calls=0;usage={'total_tokens':12}
            def design(self,kind,request):self.calls+=1;return {'keep':False,'name':'控制理论实验记录.txt','reason':'正文标题表明实验用途'}
        provider=Provider();manager=AIManager(self.app,FakeVault(),lambda *a,**k:provider);manager.configure({'enabled':True})
        p=self.file();packet=renaming.ai_prepare(self.app,manager,'00_收件箱/'+p.name,'这是实验记录')
        result=run(self.app,manager,packet);self.assertEqual(result['suggested'],'控制理论实验记录.txt');self.assertTrue(p.exists());self.assertEqual(manager.status()['calls_today'],1)
        p.write_text('changed')
        with self.assertRaises(SafetyError):run(self.app,manager,packet)
        self.assertEqual(provider.calls,1)
