import unittest,tempfile,json,os,zipfile
from pathlib import Path
from unittest.mock import patch
from personalstorage.common import *
from personalstorage.policy import Policy,REQUIRED_CONSTRAINTS
from personalstorage.state import State
from personalstorage.planner import build_plan
from personalstorage.executor import execute
from personalstorage.recovery import recover,undo
from personalstorage.discovery import discover,baseline
from personalstorage.extraction import extract
from personalstorage.review import export_tasks,import_results
from personalstorage import reports

def config():
    return {'schema_version':'3.0','policy_version':'3.0.0','constraints':{k:True for k in REQUIRED_CONSTRAINTS},'categories':[{'root':p} for p in ['00_收件箱','01_课程学习','02_项目','03_竞赛','04_学生工作与实践','05_升学与交流','06_资料库','07_个人资料','08_软件与工具','09_移动硬盘管理','99_临时待整理']],'review_path':'00_收件箱/待确认','tasks':[],'executable_rules':[{'id':'note','all':[{'field':'name','op':'equals','value':'note.txt'},{'field':'text','op':'contains','value':'course'}],'target_parent':'01_课程学习/测试课程'}]}

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='personalstorage-test-')
        self.base=Path(self.temp.name); self.root=self.base/'library'; self.root.mkdir()
        (self.root/'00_收件箱').mkdir(); self.c=config()
        write_json(self.root/'_整理规则/分类配置.json',self.c)
        self.state=State(self.root,self.base/'profile'); self.policy=Policy(self.root)
    def tearDown(self): self.state.close(); self.temp.cleanup()
    def file(self,relative='00_收件箱/note.txt',content='course material'):
        p=self.root/relative; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(content,encoding='utf-8'); return p
    def plan(self,**kw): return build_plan(self.root,self.state,self.policy,stable_seconds=0,**kw)
    def apply(self,plan): return execute(self.root,self.state,self.policy,plan['id'])
    def test_unknown_schema_fails_closed(self):
        self.c['schema_version']='1.0'; write_json(self.root/'_整理规则/分类配置.json',self.c)
        with self.assertRaises(SafetyError): Policy(self.root)
    def test_missing_constraint_fails_closed(self):
        self.c['constraints']['no_delete']=False; write_json(self.root/'_整理规则/分类配置.json',self.c)
        with self.assertRaises(SafetyError): Policy(self.root)
    def test_invalid_operator_rejected(self):
        self.c['executable_rules'][0]['all'][0]['op']='eval'; write_json(self.root/'_整理规则/分类配置.json',self.c)
        with self.assertRaises(SafetyError): Policy(self.root)
    def test_duplicate_rule_id_rejected(self):
        self.c['executable_rules']*=2; write_json(self.root/'_整理规则/分类配置.json',self.c)
        with self.assertRaises(SafetyError): Policy(self.root)
    def test_policy_rejects_target_traversal(self):
        self.c['executable_rules'][0]['target_parent']='01_课程学习/../escape'; write_json(self.root/'_整理规则/分类配置.json',self.c)
        with self.assertRaises(SafetyError): Policy(self.root)
    def test_path_traversal_rejected(self):
        for s in ['../x','D:/x','/x','a/../x','a//x','a:stream']:
            with self.assertRaises(SafetyError): safe_path(self.root,s)
    def test_root_unchanged_in_preview(self):
        p=self.file(); h=sha(p); result=self.plan()
        self.assertEqual(sha(p),h); self.assertFalse((self.root/'01_课程学习').exists()); self.assertEqual(result['actions'][0]['decision'],'move')
    def test_scope_root_opt_in(self):
        self.file('loose.txt'); self.assertEqual(discover(self.root),[])
        self.assertEqual(discover(self.root,include_root=True),['loose.txt'])
    def test_management_excluded(self):
        self.file('AGENTS.md'); self.assertEqual(discover(self.root,True),[])
    def test_temp_download_excluded(self):
        self.file('00_收件箱/test.part'); self.assertEqual(discover(self.root),[])
    def test_review_excluded_unless_explicit(self):
        self.file('00_收件箱/待确认/a.txt')
        self.assertEqual(discover(self.root),[])
        self.assertEqual(discover(self.root,include_review=True),['00_收件箱/待确认/a.txt'])
    def test_historical_scope_requires_permission(self):
        self.file('01_课程学习/old.txt')
        p=self.plan(sources=['01_课程学习/old.txt']); self.assertEqual(p['actions'][0]['decision'],'skip')
    def test_staging_cannot_be_source(self):
        self.file('09_移动硬盘管理/待移入硬盘/a.txt')
        p=self.plan(sources=['09_移动硬盘管理/待移入硬盘/a.txt'],allow_existing=True)
        self.assertEqual(p['actions'][0]['decision'],'skip')
    def test_conflicting_matches_review(self):
        r=dict(self.c['executable_rules'][0]); r['id']='other'; r['target_parent']='02_项目/other'; self.c['executable_rules'].append(r)
        write_json(self.root/'_整理规则/分类配置.json',self.c); self.policy=Policy(self.root); self.file()
        self.assertEqual(self.plan()['actions'][0]['decision'],'review')
    def test_same_target_matches_allowed(self):
        r=dict(self.c['executable_rules'][0]); r['id']='same'; self.c['executable_rules'].append(r)
        write_json(self.root/'_整理规则/分类配置.json',self.c); self.policy=Policy(self.root); self.file()
        self.assertEqual(self.plan()['actions'][0]['decision'],'move')
    def test_unknown_review_not_guessed(self):
        self.file('00_收件箱/unknown.pdf','not a pdf')
        self.assertEqual(self.plan()['actions'][0]['decision'],'review')
    def test_manual_decision_needs_explicit_approval(self):
        self.file()
        with self.assertRaises(SafetyError): self.plan(decisions={'x':{}})
    def test_manual_stale_fingerprint_rejected(self):
        self.file(); p=self.plan(decisions={'00_收件箱/note.txt':{'manifest_hash':'wrong','target_parent':'02_项目/A','evidence':'user'}},approve_decisions=True)
        self.assertEqual(p['actions'][0]['decision'],'skip')
    def test_directory_and_member_overlap_rejected(self):
        self.file('00_收件箱/bundle/a.txt')
        with self.assertRaises(SafetyError): self.plan(sources=['00_收件箱/bundle','00_收件箱/bundle/a.txt'])
    def test_staged_task_context_held(self):
        self.c['tasks']=[{'id':'t','path':'09_移动硬盘管理/待移入硬盘/A','storage':'staging','lifecycle':'unspecified'}]
        write_json(self.root/'_整理规则/分类配置.json',self.c); self.policy=Policy(self.root)
        self.assertEqual(self.policy.classify({}, {'task_id':'t'})['decision'],'review')
    def test_completed_destination_needs_permission(self):
        with self.assertRaises(SafetyError): self.policy.target('02_项目/A（已完成）','x.txt')
    def test_active_context_overrides_subject_rule(self):
        (self.root/'02_项目/A').mkdir(parents=True)
        self.c['tasks']=[{'id':'t','path':'02_项目/A','storage':'active','lifecycle':'unspecified'}]
        write_json(self.root/'_整理规则/分类配置.json',self.c); self.policy=Policy(self.root)
        self.assertEqual(self.policy.classify({'name':'note.txt','text':'course'},{'task_id':'t'})['target_parent'],'02_项目/A')
    def test_move_preserves_content_name_time(self):
        p=self.file(); old=manifest(p); plan=self.plan(); self.apply(plan)
        dst=self.root/plan['actions'][0]['target']; self.assertEqual(manifest(dst),old); self.assertFalse(p.exists())
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM versions').fetchone()[0],1)
    def test_duplicate_application_idempotent(self):
        self.file(); plan=self.plan(); self.apply(plan); result=self.apply(plan)
        self.assertEqual(result['results'][0]['status'],'already_committed')
    def test_same_name_same_content_no_overwrite(self):
        self.file(); self.file('01_课程学习/测试课程/note.txt'); p=self.plan()
        self.assertEqual(p['actions'][0]['decision'],'conflict'); self.apply(p); self.assertTrue((self.root/'00_收件箱/note.txt').exists())
    def test_same_name_different_content_no_overwrite(self):
        self.file(); dest=self.file('01_课程学习/测试课程/note.txt','different'); p=self.plan(); self.apply(p)
        self.assertEqual(dest.read_text(),'different')
    def test_new_conflict_after_planning(self):
        self.file(); plan=self.plan(); dst=self.file(plan['actions'][0]['target'],'newer'); self.apply(plan)
        self.assertEqual(dst.read_text(),'newer'); self.assertTrue((self.root/'00_收件箱/note.txt').exists())
    def test_source_changed_before_apply(self):
        p=self.file(); plan=self.plan(); p.write_text('changed'); result=self.apply(plan)
        self.assertEqual(result['results'][0]['status'],'failed'); self.assertTrue(p.exists())
    def test_policy_changed_invalidates_plan(self):
        self.file(); plan=self.plan(); self.c['policy_version']='3.0.1'; write_json(self.root/'_整理规则/分类配置.json',self.c)
        with self.assertRaises(SafetyError): execute(self.root,self.state,Policy(self.root),plan['id'])
    def test_pause_blocks_execution(self):
        self.file(); plan=self.plan(); self.state.set('paused',True)
        with self.assertRaises(SafetyError): self.apply(plan)
    def test_directory_hidden_structure_preserved(self):
        p=self.file('00_收件箱/bundle/.git/config','git data'); self.file('00_收件箱/bundle/src/a.py','x')
        m=manifest(p.parent.parent)
        plan=self.plan(decisions={'00_收件箱/bundle':{'manifest_hash':digest(m),'target_parent':'02_项目/A','evidence':'entire project'}},approve_decisions=True)
        self.apply(plan); self.assertEqual(manifest(self.root/'02_项目/A/bundle'),m)
    def test_directory_baseline_members_follow_move(self):
        self.file('00_收件箱/bundle/a.txt'); baseline(self.root,self.state)
        m=manifest(self.root/'00_收件箱/bundle')
        p=self.plan(decisions={'00_收件箱/bundle':{'manifest_hash':digest(m),'target_parent':'02_项目/A','evidence':'project'}},approve_decisions=True)
        self.apply(p); self.assertIsNotNone(self.state.item('02_项目/A/bundle/a.txt')); self.assertIsNone(self.state.item('00_收件箱/bundle/a.txt'))
    def test_crash_after_rename_recovers_without_second_move(self):
        self.file(); plan=self.plan()
        class Crash(BaseException): pass
        def crash(*a): raise Crash()
        with self.assertRaises(Crash): execute(self.root,self.state,self.policy,plan['id'],crash_hook=crash)
        self.assertEqual(recover(self.root,self.state,self.policy)[0]['status'],'committed')
    def test_unfinished_move_blocks_next_run(self):
        self.file(); plan=self.plan(); op={**plan['actions'][0],'run_id':plan['id']}; self.state.op(op['id'],'prepared',op)
        with self.assertRaises(SafetyError): self.apply(plan)
        self.assertEqual(recover(self.root,self.state,self.policy)[0]['status'],'source_retained')
    def test_missing_both_never_mark_success(self):
        self.file(); plan=self.plan(); op={**plan['actions'][0],'run_id':plan['id']}; self.state.op(op['id'],'prepared',op)
        (self.root/op['source']).unlink() # Test-owned file only.
        self.assertEqual(recover(self.root,self.state,self.policy)[0]['status'],'manual_required')
    def test_permission_failure_keeps_source(self):
        p=self.file(); plan=self.plan()
        with patch('personalstorage.executor.rename_no_replace',side_effect=PermissionError('locked')): result=self.apply(plan)
        self.assertTrue(p.exists()); self.assertEqual(result['results'][0]['status'],'failed')
    def test_no_copy_delete_cross_volume_fallback(self):
        src=self.file(); dest=self.root/'target.txt'
        with patch('personalstorage.common.os.rename',side_effect=OSError('different device')):
            with self.assertRaises(OSError): rename_no_replace(src,dest)
        self.assertTrue(src.exists()); self.assertFalse(dest.exists())
    def test_undo_preserves_bytes(self):
        self.file(); plan=self.plan(); self.apply(plan); result=undo(self.root,self.state,self.policy,plan['actions'][0]['id'])
        self.assertEqual(result['status'],'committed'); self.assertTrue((self.root/'00_收件箱/note.txt').exists())
    def test_undo_refuses_changed_file(self):
        self.file(); plan=self.plan(); self.apply(plan); (self.root/plan['actions'][0]['target']).write_text('new')
        with self.assertRaises(SafetyError): undo(self.root,self.state,self.policy,plan['actions'][0]['id'])
    def test_undo_refuses_occupied_original(self):
        self.file(); plan=self.plan(); self.apply(plan); self.file(content='new')
        with self.assertRaises(SafetyError): undo(self.root,self.state,self.policy,plan['actions'][0]['id'])
    def test_history_baseline_not_queued_or_overwritten(self):
        self.file('01_课程学习/old.txt'); baseline(self.root,self.state)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0],0)
    def review_case(self):
        self.file(); self.apply(self.plan()); return export_tasks(self.root,self.state,self.policy,self.base/(uid()+'.json'))
    def test_review_confirm(self):
        payload=self.review_case(); payload['items'][0]['decision']='confirm'
        r=import_results(self.root,self.state,self.policy,payload); self.assertEqual(r[0]['status'],'confirmed')
    def test_review_stale_content_rejected(self):
        payload=self.review_case(); payload['items'][0]['decision']='confirm'
        (self.root/payload['items'][0]['source']).write_text('edited')
        self.assertEqual(import_results(self.root,self.state,self.policy,payload)[0]['status'],'rejected')
    def test_review_unknown_decision_rejected(self):
        payload=self.review_case(); payload['items'][0]['decision']='delete'
        self.assertEqual(import_results(self.root,self.state,self.policy,payload)[0]['status'],'rejected')
    def test_review_policy_stale_rejected(self):
        payload=self.review_case(); payload['policy_hash']='wrong'
        with self.assertRaises(SafetyError): import_results(self.root,self.state,self.policy,payload)
    def test_unavailable_review_wakes_on_return(self):
        payload=self.review_case(); p=self.root/payload['items'][0]['source']; temp=p.with_suffix('.held'); p.rename(temp)
        r=export_tasks(self.root,self.state,self.policy,self.base/'offline.json'); self.assertEqual(r['items'],[])
        temp.rename(p); r=export_tasks(self.root,self.state,self.policy,self.base/'returned.json'); self.assertEqual(len(r['items']),1)
    def test_manual_lock_protects_assignment(self):
        p=self.file(); m=manifest(p); plan=self.plan(decisions={'00_收件箱/note.txt':{'manifest_hash':digest(m),'target_parent':'02_项目/A','evidence':'user'}},approve_decisions=True)
        self.apply(plan); payload=export_tasks(self.root,self.state,self.policy,self.base/'review.json'); payload['items'][0].update(decision='suggest_reclassify',target_parent='06_资料库/C++',reason='suggest')
        self.assertEqual(import_results(self.root,self.state,self.policy,payload)[0]['status'],'rejected')
    def test_snapshot_is_consistent(self):
        self.file(); self.apply(self.plan()); target=self.base/'backup.sqlite3'; self.state.snapshot(target)
        import sqlite3
        db=sqlite3.connect(target); self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok'); db.close()
    def test_report_search_literals(self):
        self.file('01_课程学习/a_b.txt'); baseline(self.root,self.state)
        self.assertEqual(len(reports.search(self.state,'a_b')),1); self.assertIn('状态报告',reports.markdown(self.state))
    def test_archive_list_does_not_extract(self):
        p=self.root/'00_收件箱/a.zip'
        with zipfile.ZipFile(p,'w') as z: z.writestr('../escape.txt','data')
        result=extract(p); self.assertEqual(result['members'],['../escape.txt']); self.assertFalse((self.root/'escape.txt').exists())
    def test_xml_entity_rejected(self):
        p=self.root/'00_收件箱/a.docx'
        with zipfile.ZipFile(p,'w') as z: z.writestr('word/document.xml',b'<!DOCTYPE x><x/>')
        self.assertTrue(extract(p)['warnings'])
    def test_automatic_conflict_not_repeated_until_target_removed(self):
        self.file(); dst=self.file('01_课程学习/测试课程/note.txt'); plan=self.plan(); self.apply(plan)
        nextplan=self.plan(automatic=True); self.assertEqual(nextplan['actions'],[])
        dst.unlink(); nextplan=self.plan(automatic=True); self.assertEqual(nextplan['actions'][0]['decision'],'move')
    def test_explicit_source_can_retry_held_failure(self):
        self.file(); self.file('01_课程学习/测试课程/note.txt'); self.apply(self.plan())
        self.assertEqual(len(self.plan(sources=['00_收件箱/note.txt'])['actions']),1)
    def test_relevant_rule_change_queues_once(self):
        from personalstorage.maintenance import reconcile
        self.file(); self.apply(self.plan())
        self.c['executable_rules'][0]['all'][1]['value']='changed_rule'; write_json(self.root/'_整理规则/分类配置.json',self.c)
        policy=Policy(self.root); self.assertEqual(reconcile(self.root,self.state,policy)[0]['status'],'rule_changed')
        self.assertEqual(reconcile(self.root,self.state,policy)[0]['status'],'unchanged')
        self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM reviews WHERE status='pending'").fetchone()[0],1)
    def test_unrelated_rule_does_not_requeue_confirmed(self):
        from personalstorage.maintenance import reconcile
        payload=self.review_case(); payload['items'][0]['decision']='confirm'; import_results(self.root,self.state,self.policy,payload)
        self.c['executable_rules'].append({'id':'irrelevant','all':[{'field':'name','op':'equals','value':'different.txt'}],'target_parent':'02_项目/B'})
        write_json(self.root/'_整理规则/分类配置.json',self.c)
        self.assertEqual(reconcile(self.root,self.state,Policy(self.root))[0]['status'],'unchanged')
        self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM reviews WHERE status='pending'").fetchone()[0],0)
    def test_changed_registered_content_requeues_without_move(self):
        from personalstorage.maintenance import reconcile
        self.file(); p=self.plan(); self.apply(p); dest=self.root/p['actions'][0]['target']; dest.write_text('new content')
        self.assertEqual(reconcile(self.root,self.state,self.policy)[0]['status'],'changed'); self.assertTrue(dest.exists())
    def test_missing_path_is_not_deleted(self):
        from personalstorage.maintenance import reconcile
        self.file(); p=self.plan(); self.apply(p); dest=self.root/p['actions'][0]['target']; dest.rename(dest.with_suffix('.moved'))
        self.assertEqual(reconcile(self.root,self.state,self.policy)[0]['status'],'missing_observed'); self.assertIsNotNone(self.state.item(p['actions'][0]['target']))
    def test_bad_review_items_rejected(self):
        with self.assertRaises(SafetyError): import_results(self.root,self.state,self.policy,{'schema_version':'1.0','policy_hash':self.policy.hash,'items':'not a list'})
    def test_export_refuses_overwriting_existing_file(self):
        target=self.base/'data.json'; target.write_text('original')
        with self.assertRaises(SafetyError): export_tasks(self.root,self.state,self.policy,target)
        self.assertEqual(target.read_text(),'original')
    def test_source_link_rejected(self):
        from personalstorage import common
        p=self.file()
        with patch('personalstorage.common.linked',side_effect=lambda x: Path(x)==p):
            with self.assertRaises(SafetyError): common.safe_path(self.root,'00_收件箱/note.txt')
    def test_lock_excludes_another_executor(self):
        with instance_lock(self.state.profile):
            with self.assertRaises(SafetyError):
                with instance_lock(self.state.profile): pass
    def test_management_case_variant_still_protected(self):
        self.file('AGENTS.md')
        self.assertEqual(self.plan(include_root=True,sources=['agents.md'])['actions'][0]['decision'],'skip')
    def test_missing_task_path_not_recreated(self):
        self.c['tasks']=[{'id':'t','path':'02_项目/missing','storage':'active','lifecycle':'unspecified'}]
        write_json(self.root/'_整理规则/分类配置.json',self.c)
        self.assertEqual(Policy(self.root).classify({}, {'task_id':'t'})['decision'],'review')
    def test_wrong_case_source_cannot_rename_original(self):
        self.file('00_收件箱/Note.txt')
        p=self.plan(sources=['00_收件箱/note.txt'])
        self.apply(p); self.assertTrue((self.root/'00_收件箱/Note.txt').exists())
    def cli(self,*args):
        import subprocess,sys
        return subprocess.run([sys.executable,'-X','utf8','-m','personalstorage','--root',str(self.root),'--profile',str(self.state.profile),*args],capture_output=True,text=True,encoding='utf-8')
    def test_cli_plan_apply_status_roundtrip(self):
        self.file(); p=self.cli('plan','--stable-seconds','0'); self.assertEqual(p.returncode,0,p.stderr)
        plan=json.loads(p.stdout); r=self.cli('apply',plan['id'],'--approve'); self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads(self.cli('status').stdout)['operations']['committed'],1)
    def test_cli_apply_requires_explicit_approval(self):
        self.assertNotEqual(self.cli('apply','some-plan').returncode,0)
    def test_cli_authorize_poll_once_and_revoke(self):
        self.file(); self.assertEqual(self.cli('authorize','--approve').returncode,0)
        result=self.cli('watch','--once'); self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.root/'01_课程学习/测试课程/note.txt').exists())
        self.assertEqual(self.cli('revoke').returncode,0)
        self.assertNotEqual(self.cli('watch','--once').returncode,0)
    def test_cli_snapshot_restore_leaves_original_files(self):
        p=self.file(); h=sha(p); snap=self.base/'state-copy.sqlite3'
        self.assertEqual(self.cli('snapshot','--output',str(snap)).returncode,0)
        self.assertEqual(self.cli('pause').returncode,0)
        r=self.cli('restore-state',str(snap),'--approve'); self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(sha(p),h); self.assertFalse(json.loads(self.cli('status').stdout)['paused'])
    def test_unclassified_item_cannot_be_confirmed_without_destination(self):
        self.file('00_收件箱/unknown.txt','unknown'); self.apply(self.plan()); packet=export_tasks(self.root,self.state,self.policy,self.base/'pending.json')
        packet['items'][0]['decision']='confirm'
        self.assertEqual(import_results(self.root,self.state,self.policy,packet)[0]['status'],'rejected')
    def test_changed_content_export_uses_fresh_version_immediately(self):
        packet=self.review_case(); p=self.root/packet['items'][0]['source']; p.write_text('edited contents')
        fresh=export_tasks(self.root,self.state,self.policy,self.base/'fresh.json')
        self.assertEqual(len(fresh['items']),1)
        self.assertEqual(fresh['items'][0]['expected_version'],digest(manifest(p)))
        self.assertEqual(json.loads(self.state.item(fresh['items'][0]['source'])['data']),manifest(p))
    def test_management_snapshot_includes_policy_not_original_content(self):
        from personalstorage.snapshot import management_snapshot
        self.file(content='PRIVATE_ORIGINAL_CONTENT')
        result=management_snapshot(self.root,self.state,self.policy,self.base/'bundle.sqlite3')
        text=Path(result['metadata_and_rule_texts']).read_text(encoding='utf-8')
        self.assertIn('分类配置.json',text); self.assertNotIn('PRIVATE_ORIGINAL_CONTENT',text)
    def test_parser_timeout_preserves_original(self):
        p=self.file('00_收件箱/a.pdf','invalid test-only pdf'); before=sha(p)
        result=extract(p,timeout=0.001)
        self.assertTrue(any('超时' in s for s in result['warnings'])); self.assertEqual(sha(p),before)
    def test_docx_readonly_worker_extracts_text(self):
        p=self.root/'00_收件箱/example.docx'
        with zipfile.ZipFile(p,'w') as z: z.writestr('word/document.xml','<w:document xmlns:w="urn:word"><w:t>测试正文</w:t></w:document>')
        before=sha(p); self.assertIn('测试正文',extract(p)['text']); self.assertEqual(sha(p),before)
    def test_reserved_application_and_disabled_model_interfaces(self):
        from personalstorage.service import Application
        from personalstorage.model_client import DisabledProvider
        app=Application(self.root,self.state.profile)
        try:
            self.assertEqual(app.status()['root'],str(self.root)); self.assertEqual(app.pending(),[])
            result=app.snapshot(self.base/'api-snapshot.sqlite3')
            self.assertTrue(Path(result['metadata_and_rule_texts']).exists())
            with self.assertRaises(NotImplementedError): DisabledProvider().review({})
        finally: app.close()
    def test_readable_manifest_and_phase_logs_written(self):
        self.file(); p=self.plan(); self.apply(p)
        folder=self.root/'_整理规则/执行记录'/p['id']
        self.assertTrue((folder/'source_manifest.json').is_file())
        events=[json.loads(x) for x in (folder/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([x['status'] for x in events],['prepared','moved','verified','committed'])
    def test_committed_registration_is_idempotent(self):
        self.file(); p=self.plan(); self.apply(p)
        row=self.state.db.execute('SELECT data FROM operations WHERE id=?',(p['actions'][0]['id'],)).fetchone()
        before=self.state.item(p['actions'][0]['target'])['id']; self.state.register(json.loads(row['data']))
        self.assertEqual(self.state.item(p['actions'][0]['target'])['id'],before)
        self.assertEqual(self.state.db.execute('SELECT version FROM assignments WHERE item_id=?',(before,)).fetchone()[0],1)

if __name__=='__main__': unittest.main(verbosity=2)
