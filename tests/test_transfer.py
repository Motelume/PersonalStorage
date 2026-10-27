"""Only synthetic, isolated files are deleted by these transfer tests."""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch
from personalstorage import inboxes,importing,transfer
from personalstorage.common import manifest,SafetyError
import test_intelligent as fixtures

class TransferTests(unittest.TestCase):
    setUp=fixtures.IntelligentTests.setUp
    tearDown=fixtures.IntelligentTests.tearDown
    add=fixtures.IntelligentTests.add
    external=fixtures.IntelligentTests.external

    def plan(self,bundle=False,mode='move'):
        record=inboxes.add(self.root,self.app.state,str(self.downloads),'测试来源',mode)
        source=self.downloads/('资料包' if bundle else 'note.txt')
        if bundle:
            (source/'子目录/空目录').mkdir(parents=True)
            (source/'a.txt').write_bytes(b'a')
            (source/'子目录/b.txt').write_bytes(b'b')
        else:source.write_bytes(b'course')
        logical=inboxes.PREFIX+record['id']+'/'+source.name
        plan=importing.build(self.app,[{'path':logical}],target_parent='01_课程学习/测试课程',evidence='测试归属')
        return record,source,plan

    def test_file_transfer_verifies_then_deletes_source(self):
        record,source,plan=self.plan();before=manifest(source)
        result=importing.execute(self.app,plan)['results'][0]
        self.assertEqual(result['status'],'committed',result)
        self.assertFalse(source.exists());self.assertFalse(result['source_retained'])
        self.assertEqual(manifest(self.root/result['target']),before)
        self.assertEqual(self.app.state.db.execute('SELECT COUNT(*) FROM inbox_imports').fetchone()[0],0)

    def test_bundle_transfer_preserves_every_member_and_empty_directory(self):
        _,source,plan=self.plan(True);before=manifest(source)
        result=importing.execute(self.app,plan)['results'][0]
        self.assertEqual(result['status'],'committed',result)
        self.assertFalse(source.exists());self.assertEqual(manifest(self.root/result['target']),before)

    def test_existing_sources_without_mode_default_to_copy(self):
        record,source,plan=self.plan(mode='copy')
        data=inboxes.settings(self.app.state);data['sources'][0].pop('import_mode');self.app.state.set('inboxes',data)
        self.assertEqual(inboxes.mode(self.app.state,record['id']),'copy')
        self.assertEqual(importing.execute(self.app,plan)['results'][0]['status'],'committed')
        self.assertTrue(source.exists())

    def test_mode_changes_invalidate_even_after_switching_back(self):
        record,source,plan=self.plan()
        inboxes.set_mode(self.app.state,record['id'],'copy');inboxes.set_mode(self.app.state,record['id'],'move')
        result=importing.execute(self.app,plan)['results'][0]
        self.assertEqual(result['status'],'failed');self.assertTrue(source.exists())
        self.assertFalse((self.root/plan['actions'][0]['target']).exists())

    def test_changed_source_before_cleanup_retains_it(self):
        _,source,plan=self.plan()
        def hook(phase,op):
            if phase=='after_target_verified':source.write_bytes(b'new incoming content')
        result=importing.execute(self.app,plan,crash_hook=hook)['results'][0]
        self.assertEqual(result['status'],'source_retained',result)
        self.assertEqual(source.read_bytes(),b'new incoming content')
        self.assertEqual((self.root/result['target']).read_bytes(),b'course')
        with self.assertRaises(SafetyError):importing.cleanup_preview(self.app,result['operation_id'])

    def test_occupied_original_is_retained_and_explicit_retry_succeeds(self):
        _,source,plan=self.plan();before=manifest(source)
        with transfer.locked_file(source):result=importing.execute(self.app,plan)['results'][0]
        self.assertEqual(result['status'],'source_retained',result);self.assertEqual(manifest(source),before)
        ticket=importing.cleanup_preview(self.app,result['operation_id'])
        retried=importing.cleanup_execute(self.app,ticket)
        self.assertEqual(retried['status'],'committed',retried);self.assertFalse(source.exists())
        self.assertEqual(self.app.state.db.execute('SELECT version FROM assignments').fetchone()[0],1)

    def test_target_cannot_be_rewritten_during_source_deletion(self):
        _,source,plan=self.plan(True);blocked=[]
        def hook(phase,op):
            if phase=='after_source_member_removed':
                try:(self.root/op['target']/'a.txt').write_bytes(b'corrupt')
                except OSError:blocked.append(True)
        result=importing.execute(self.app,plan,crash_hook=hook)['results'][0]
        self.assertEqual(result['status'],'committed',result);self.assertEqual(len(blocked),2)

    def test_new_incoming_file_is_never_deleted(self):
        _,source,plan=self.plan(True)
        def hook(phase,op):
            if phase=='after_source_member_removed':(source/'new.txt').write_bytes(b'new arrival')
        result=importing.execute(self.app,plan,crash_hook=hook)['results'][0]
        self.assertEqual(result['status'],'source_retained',result)
        self.assertEqual((source/'new.txt').read_bytes(),b'new arrival')
        self.assertEqual(manifest(self.root/result['target']),plan['actions'][0]['manifest'])
        with self.assertRaises(SafetyError):importing.cleanup_preview(self.app,result['operation_id'])

    def test_crash_recovery_does_not_delete_remainder_and_retry_finishes(self):
        _,source,plan=self.plan(True)
        class Crash(BaseException):pass
        def hook(phase,op):
            if phase=='after_source_member_removed':raise Crash()
        with self.assertRaises(Crash):importing.execute(self.app,plan,crash_hook=hook)
        remaining=manifest(source);self.assertEqual(len(remaining['files']),1)
        recovered=self.app.recover();self.assertEqual(recovered[0]['status'],'source_retained',recovered)
        self.assertEqual(manifest(source),remaining)
        opid=plan['actions'][0]['id'];ticket=importing.cleanup_preview(self.app,opid)
        result=importing.cleanup_execute(self.app,ticket)
        self.assertEqual(result['status'],'committed',result);self.assertFalse(source.exists())

    def test_copy_then_move_reuses_only_its_verified_archive(self):
        record,source,plan=self.plan(mode='copy');importing.execute(self.app,plan)
        inboxes.set_mode(self.app.state,record['id'],'move')
        rows=inboxes.scan_external(self.root,self.app.state,self.app.policy,{})['rows']
        self.assertEqual(len(rows),1);self.assertTrue(rows[0]['reuse_import_target'])
        next_plan=importing.build(self.app,[{'path':rows[0]['source'],'action':rows[0]}])
        self.assertTrue(next_plan['actions'][0]['reuse_existing'])
        with patch('personalstorage.importing.shutil.copyfileobj',side_effect=AssertionError('must not copy again')):
            result=importing.execute(self.app,next_plan)['results'][0]
        self.assertEqual(result['status'],'committed',result);self.assertFalse(source.exists())
        self.assertEqual(self.app.state.db.execute('SELECT version FROM assignments').fetchone()[0],1)

    def test_unrelated_existing_file_cannot_authorize_source_deletion(self):
        _,source,plan=self.plan();shutil.copy2(source,self.root/plan['actions'][0]['target'])
        with self.assertRaises(SafetyError):importing.build(self.app,[{'path':plan['actions'][0]['source']}],target_parent='01_课程学习/测试课程')
        self.assertTrue(source.exists())

    def test_changed_mode_invalidates_cleanup_ticket(self):
        record,source,plan=self.plan()
        with transfer.locked_file(source):result=importing.execute(self.app,plan)['results'][0]
        ticket=importing.cleanup_preview(self.app,result['operation_id']);inboxes.set_mode(self.app.state,record['id'],'copy')
        with self.assertRaises(SafetyError):importing.cleanup_execute(self.app,ticket)
        self.assertTrue(source.exists())

    def test_repeated_execute_cannot_replace_completed_audit(self):
        _,source,plan=self.plan();importing.execute(self.app,plan)
        with self.assertRaises(SafetyError):importing.execute(self.app,plan)
        self.assertEqual(self.app.state.db.execute('SELECT status FROM operations').fetchone()[0],'committed')

    def test_corrupt_target_before_cleanup_never_deletes_original(self):
        _,source,plan=self.plan()
        def hook(phase,op):
            if phase=='after_target_verified':(self.root/op['target']).write_bytes(b'corrupted')
        result=importing.execute(self.app,plan,crash_hook=hook)['results'][0]
        self.assertEqual(result['status'],'recovery_required',result);self.assertEqual(source.read_bytes(),b'course')

    def test_readonly_source_is_retained_with_verified_target(self):
        import stat
        _,source,plan=self.plan();source.chmod(stat.S_IREAD)
        try:
            result=importing.execute(self.app,plan)['results'][0]
            self.assertEqual(result['status'],'source_retained',result);self.assertTrue(source.exists())
            self.assertEqual(manifest(self.root/result['target']),plan['actions'][0]['manifest'])
        finally:source.chmod(stat.S_IWRITE)

    def test_mode_switch_during_package_cleanup_preserves_remainder(self):
        record,source,plan=self.plan(True)
        def hook(phase,op):
            if phase=='after_source_member_removed':inboxes.set_mode(self.app.state,record['id'],'copy')
        result=importing.execute(self.app,plan,crash_hook=hook)['results'][0]
        self.assertEqual(result['status'],'source_retained',result)
        self.assertEqual(len(manifest(source)['files']),1)
        self.assertEqual(manifest(self.root/result['target']),plan['actions'][0]['manifest'])

    def test_real_cross_volume_transfer_with_only_synthetic_file(self):
        from pathlib import Path
        other=next((Path(letter+':/') for letter in 'DEFG' if Path(letter+':/').is_dir() and Path(letter+':/').stat().st_dev!=self.root.stat().st_dev),None)
        if other is None:self.skipTest('No second local volume')
        with tempfile.TemporaryDirectory(prefix='PersonalStorage-transfer-test-',dir=other) as tmp:
            folder=Path(tmp);source=folder/'synthetic.txt';source.write_bytes(b'synthetic cross-volume fixture')
            record=inboxes.add(self.root,self.app.state,str(folder),'跨盘测试','move')
            plan=importing.build(self.app,[{'path':inboxes.PREFIX+record['id']+'/'+source.name}],target_parent='01_课程学习/测试课程')
            result=importing.execute(self.app,plan)['results'][0]
            self.assertEqual(result['status'],'committed',result);self.assertFalse(source.exists())
            self.assertEqual((self.root/result['target']).read_bytes(),b'synthetic cross-volume fixture')
