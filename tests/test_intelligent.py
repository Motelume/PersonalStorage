import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from personalstorage.common import SafetyError,write_json,manifest,digest
from personalstorage.policy import Policy
from personalstorage.service import Application
from personalstorage.ai import AIManager
from personalstorage import inboxes,importing,ai_audit,classification
from test_core import config
from test_ai import FakeVault,FakeProvider

class IntelligentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'library'
        (self.root/'00_收件箱').mkdir(parents=True);(self.root/'01_课程学习/测试课程').mkdir(parents=True)
        write_json(self.root/'_整理规则/分类配置.json',config())
        self.app=Application(self.root,self.base/'profile');self.downloads=self.base/'downloads';self.downloads.mkdir()
        self.provider=FakeProvider();self.manager=AIManager(self.app,FakeVault(),lambda *a,**k:self.provider);self.manager.configure({'enabled':True})
    def tearDown(self):self.app.close();self.tmp.cleanup()

    def add(self):return inboxes.add(self.root,self.app.state,str(self.downloads),'下载示例')
    def external(self,name='download.txt'):
        self.add();(self.downloads/name).write_text('course',encoding='utf-8')
        return inboxes.scan_external(self.root,self.app.state,self.app.policy,{})['rows'][0]

    def test_explicit_inboxes_and_overlap_protection(self):
        self.assertEqual(len(inboxes.listing(self.root,self.app.state)['sources']),1)
        record=self.add();self.assertTrue(record['enabled'])
        for path in (self.root,self.base,self.root/'00_收件箱'):
            with self.assertRaises(SafetyError):inboxes.add(self.root,self.app.state,str(path))
        with self.assertRaises(SafetyError):self.add()
        inboxes.toggle(self.app.state,record['id'],False)
        self.assertFalse(inboxes.settings(self.app.state)['sources'][0]['enabled'])
        self.assertTrue(self.downloads.exists())

    def test_external_source_is_opaque_and_identity_bound(self):
        row=self.external();actual=inboxes.resolve(self.root,self.app.state,row['source'])
        self.assertEqual(actual,self.downloads/'download.txt')
        with self.assertRaises(SafetyError):inboxes.resolve(self.root,self.app.state,row['source']+'/../outside')
        with self.assertRaises(SafetyError):inboxes.resolve(self.root,self.app.state,'@inbox/'+'0'*32+'/download.txt')
        record=inboxes.settings(self.app.state)['sources'][0];inboxes.toggle(self.app.state,record['id'],False)
        with self.assertRaises(SafetyError):inboxes.resolve(self.root,self.app.state,row['source'])

    def test_verified_copy_keeps_original_and_skips_next_scan(self):
        row=self.external();source=self.downloads/'download.txt';before=manifest(source)
        obj={'path':row['source'],'action':row,'scan_fingerprint':row['scan_fingerprint']}
        plan=importing.build(self.app,[obj],target_parent='01_课程学习/测试课程',evidence='用户明确确认课程用途');result=importing.execute(self.app,plan)
        self.assertEqual(result['results'][0]['status'],'committed');self.assertEqual(manifest(source),before)
        self.assertEqual(manifest(self.root/'01_课程学习/测试课程/download.txt'),before)
        again=inboxes.scan_external(self.root,self.app.state,self.app.policy,{})
        self.assertEqual(again['rows'],[]);self.assertEqual(again['already_imported'],1)

    def test_copy_refuses_conflict_and_changed_source(self):
        row=self.external();obj={'path':row['source'],'action':row,'scan_fingerprint':row['scan_fingerprint']}
        plan=importing.build(self.app,[obj],target_parent='01_课程学习/测试课程',evidence='用户明确确认课程用途');(self.downloads/'download.txt').write_text('changed')
        result=importing.execute(self.app,plan);self.assertEqual(result['results'][0]['status'],'failed')
        self.assertFalse((self.root/'01_课程学习/测试课程/download.txt').exists())
        (self.root/'01_课程学习/测试课程/download.txt').write_text('existing')
        with self.assertRaises(SafetyError):importing.build(self.app,[obj])

    def test_notes_are_sent_as_context_without_authorizing_operations(self):
        p=self.root/'00_收件箱/unknown.txt';p.write_text('ambiguous')
        packet=self.manager.prepare([{'path':'00_收件箱/unknown.txt','action':{'decision':'review'}}],{'00_收件箱/unknown.txt':'这是测试课程的专用学习笔记'})
        self.assertEqual(packet['request']['documents'][0]['user_context'],'这是测试课程的专用学习笔记')
        result=self.manager.run(packet);self.assertEqual(result['results'][0]['status'],'correction_required');self.assertTrue(p.exists())
        with self.assertRaises(SafetyError):self.manager.prepare([{'path':'00_收件箱/unknown.txt','action':{'decision':'review'}}],{'00_收件箱/unknown.txt':'x'*2001})

    def test_fallback_promotes_only_unresolved_files(self):
        p=self.root/'00_收件箱/unknown.txt';p.write_text('unknown')
        row={'source':'00_收件箱/unknown.txt','decision':'review'};obj={'path':row['source'],'action':row}
        report=classification.fallback(self.app,self.manager,[row],[obj],{})
        self.assertEqual(report['ai_classified'],1);self.assertEqual(row['decision'],'move');self.assertTrue(p.exists())
        self.assertEqual(self.provider.calls,1)

    def test_whole_audit_approved_cache_skips_tokens_and_invalidates_changes(self):
        p=self.root/'01_课程学习/测试课程/note.txt';p.write_text('课程笔记')
        first=ai_audit.run(self.app,self.manager,{},threading.Event(),2)
        self.assertEqual(first['approved'],1);self.assertEqual(self.provider.calls,1)
        second=ai_audit.run(self.app,self.manager,{},threading.Event(),2)
        self.assertEqual(second['cache_hits'],1);self.assertEqual(self.provider.calls,1)
        old=p.stat();p.write_text('内容改变');os.utime(p,ns=(old.st_atime_ns,old.st_mtime_ns))
        ai_audit.run(self.app,self.manager,{},threading.Event(),2);self.assertEqual(self.provider.calls,2)
        renamed=p.with_name('renamed.txt');p.rename(renamed)
        ai_audit.run(self.app,self.manager,{},threading.Event(),2);self.assertEqual(self.provider.calls,3)

    def test_whole_audit_is_cancelable_without_moving(self):
        p=self.root/'01_课程学习/测试课程/note.txt';p.write_text('课程笔记')
        stop=threading.Event();stop.set();report=ai_audit.run(self.app,self.manager,{},stop,2)
        self.assertEqual(report['status'],'stopped');self.assertEqual(self.provider.calls,0);self.assertTrue(p.exists())

    def test_cache_survives_restart_and_rule_changes_require_new_review(self):
        p=self.root/'01_课程学习/测试课程/note.txt';p.write_text('课程笔记')
        ai_audit.run(self.app,self.manager,{},threading.Event(),2)
        self.app.close();self.app=Application(self.root,self.base/'profile');self.manager=AIManager(self.app,FakeVault(),lambda *a,**k:self.provider)
        ai_audit.run(self.app,self.manager,{},threading.Event(),2);self.assertEqual(self.provider.calls,1)
        c=config();c['policy_version']='new-rules';write_json(self.root/'_整理规则/分类配置.json',c);self.app.policy=Policy(self.root)
        ai_audit.run(self.app,self.manager,{},threading.Event(),2);self.assertEqual(self.provider.calls,2)

    def test_manual_lock_needs_explicit_one_item_override(self):
        (self.root/'06_资料库/学科资料').mkdir(parents=True)
        p=self.root/'00_收件箱/private.txt';p.write_text('课程学习资料');m=manifest(p)
        plan=self.app.plan(sources=['00_收件箱/private.txt'],stable_seconds=0,decisions={'00_收件箱/private.txt':{'manifest_hash':digest(m),'target_parent':'06_资料库/学科资料','evidence':'原先人工决定'}},approve_decisions=True)
        self.app.execute(plan['id']);pending=self.app.pending()[0];obj={'path':pending['path'],'review_id':pending['review_id']}
        packet=self.manager.prepare([obj],{obj['path']:'这是课程专用材料，请重新判断'},allow_manual_override=True)
        result=self.manager.run(packet);self.assertEqual(result['results'][0]['status'],'correction_required')
        self.assertTrue((self.root/obj['path']).exists())
        assignment=json.loads(self.app.state.db.execute('SELECT data FROM assignments').fetchone()[0]);self.assertTrue(assignment['manual_locked'])

    def test_changed_note_rejects_stale_response(self):
        p=self.root/'00_收件箱/private.txt';p.write_text('课程资料')
        packet=self.manager.prepare([{'path':'00_收件箱/private.txt','action':{'decision':'review'}}],{'00_收件箱/private.txt':'旧说明'})
        self.app.state.set('classification_notes',{'00_收件箱/private.txt':'新说明'})
        with self.assertRaises(SafetyError):self.manager.run(packet)
        self.assertEqual(self.provider.calls,0)

    def test_bundle_copy_preserves_tree_and_originals(self):
        record=self.add();bundle=self.downloads/'完整资料包';(bundle/'素材').mkdir(parents=True)
        (bundle/'素材/image.dat').write_bytes(b'opaque-image');(bundle/'README.md').write_text('完整工程说明')
        before=manifest(bundle);source=inboxes.PREFIX+record['id']+'/'+bundle.name
        plan=importing.build(self.app,[{'path':source,'action':{}}],target_parent='01_课程学习/测试课程',evidence='完整课程资料包')
        result=importing.execute(self.app,plan);self.assertEqual(result['results'][0]['status'],'committed')
        self.assertEqual(manifest(bundle),before);self.assertEqual(manifest(self.root/'01_课程学习/测试课程'/bundle.name),before)

    def test_copy_recovery_accepts_verified_target_while_original_exists(self):
        import shutil
        row=self.external();obj={'path':row['source'],'action':row}
        plan=importing.build(self.app,[obj],target_parent='01_课程学习/测试课程',evidence='已确认归属')
        action=plan['actions'][0];target=self.root/action['target'];shutil.copy2(self.downloads/'download.txt',target)
        op={**action,'run_id':plan['id'],'policy_hash':self.app.policy.hash,'policy_version':'3.0.0','scope':plan['scope']}
        self.app.state.op(action['id'],'moved',op)
        recovered=self.app.recover();self.assertEqual(recovered[0]['status'],'committed')
        self.assertTrue((self.downloads/'download.txt').exists());self.assertTrue(target.exists())

    def test_notes_can_confirm_historical_position_without_fake_assignment(self):
        p=self.root/'01_课程学习/测试课程/historical.txt';p.write_text('课程材料');m=manifest(p)
        classification.human_task(self.app,'01_课程学习/测试课程/historical.txt',m,'等待补充用途')
        row=self.app.pending()[0];self.provider.decision='confirm'
        packet=self.manager.prepare([{'path':row['path'],'review_id':row['review_id']}],{row['path']:'确实是此课程资料'})
        result=self.manager.run(packet);self.assertEqual(result['results'][0]['status'],'confirmed')
        self.assertEqual(self.app.pending(),[]);self.assertTrue(p.exists())

    def test_daily_budget_is_independent_of_recent_history_retention(self):
        p=self.root/'00_收件箱/unknown.txt';p.write_text('课程资料')
        self.manager.configure({'daily_limit':1})
        obj={'path':'00_收件箱/unknown.txt','action':{'decision':'review'}}
        self.manager.run(self.manager.prepare([obj]));self.app.state.set('ai_history',[])
        self.assertEqual(self.manager.status()['calls_today'],1)
        with self.assertRaises(SafetyError):self.manager.run(self.manager.prepare([obj]))
        self.assertEqual(self.provider.calls,1)

    def test_removing_source_keeps_files_and_closes_only_its_pending_tasks(self):
        row=self.external();m=manifest(self.downloads/'download.txt')
        classification.human_task(self.app,row['source'],m,'来源用途待确认')
        source_id=row['source'].split('/')[1];result=inboxes.remove(self.app.state,source_id)
        self.assertFalse(result['files_touched']);self.assertTrue((self.downloads/'download.txt').exists())
        self.assertEqual(len(inboxes.listing(self.root,self.app.state)['sources']),1)
        self.assertEqual(self.app.pending(),[])

    def test_whole_audit_covers_loose_and_staged_data_but_not_management(self):
        for rel in ('loose.txt','99_临时待整理/用途待确认/example.txt','09_移动硬盘管理/待移入硬盘/cold.txt','09_移动硬盘管理/硬盘内容索引/index.json'):
            path=self.root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic')
        found=ai_audit.units(self.app)
        self.assertIn('loose.txt',found);self.assertIn('99_临时待整理/用途待确认/example.txt',found)
        self.assertIn('09_移动硬盘管理/待移入硬盘/cold.txt',found)
        self.assertNotIn('09_移动硬盘管理/硬盘内容索引/index.json',found)
        self.assertFalse(any(p.startswith('_整理规则') for p in found))

    def test_new_ai_question_after_confirmation_gets_a_fresh_pending_context(self):
        p=self.root/'00_收件箱/note.txt';p.write_text('course');m=manifest(p)
        packet=self.manager.prepare([{'path':'00_收件箱/note.txt','action':{'decision':'review'}}]);self.manager.run(packet)
        plan=self.app.plan(sources=['00_收件箱/note.txt'],stable_seconds=0);self.app.execute(plan['id'])
        path='01_课程学习/测试课程/note.txt';obj={'path':path,'audit_mode':True,'action':{'decision':'review'}}
        self.provider.decision='confirm';self.manager.run(self.manager.prepare([obj]))
        self.provider.decision='needs_user_input';result=self.manager.run(self.manager.prepare([obj],{path:'需要结合新的用途再确认'}))
        self.assertEqual(result['results'][0]['status'],'waiting_user')
        self.assertTrue(any(r['path']==path for r in self.app.pending()))
