import copy
import hashlib
import json
import struct
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from personalstorage.common import SafetyError,write_json,read_json,manifest
from personalstorage.service import Application
from personalstorage.policy import Policy
from personalstorage import searching,backup,lifecycle,scanning
from personalstorage.conditions import expression
from personalstorage.extra_formats import doc_text,ppt_text,read
from personalstorage.tasks import scope,Cancelled
from test_core import config


class BetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'library'
        (self.root/'00_收件箱').mkdir(parents=True);(self.root/'01_课程学习/测试课程').mkdir(parents=True)
        self.cfg=config();write_json(self.root/'_整理规则/分类配置.json',self.cfg);self.app=Application(self.root,self.base/'state')
    def tearDown(self):self.app.close();self.tmp.cleanup()
    def file(self,path,text):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8');return p
    def policy(self,node):
        cfg=copy.deepcopy(self.cfg);cfg['executable_rules'][0].pop('all');cfg['executable_rules'][0]['when']=node;return Policy.from_data(self.root,cfg)
    def test_nested_boolean_and_missing_negation(self):
        leaf={'field':'text','op':'contains','value':'course'}
        p=self.policy({'all':[{'any':[leaf,{'field':'extension','op':'equals','value':'.pdf'}]},{'not':{'field':'name','op':'contains','value':'draft'}}]})
        self.assertEqual(p.classify({'name':'final.pdf','text':'unrelated'})['decision'],'move')
        self.assertEqual(p.classify({'name':'draft.pdf','text':'course'})['decision'],'review')
        p=self.policy({'not':{'field':'context.purpose','op':'equals','value':'unwanted'}})
        self.assertEqual(p.classify({'name':'x'})['decision'],'review')
    def test_invalid_deep_tree_and_ambiguous_protocol_rejected(self):
        node={'field':'name','op':'equals','value':'a'}
        for _ in range(8):node={'not':node}
        with self.assertRaises(SafetyError):self.policy(node)
        cfg=copy.deepcopy(self.cfg);cfg['executable_rules'][0]['when']={'any':[]}
        with self.assertRaises(SafetyError):Policy.from_data(self.root,cfg)
    def test_fulltext_chinese_literal_and_fts_syntax_is_data(self):
        p=self.file('06_资料库/学科资料/控制.txt','这是控制理论课程笔记，系统稳定性分析。')
        before=p.read_bytes();r=searching.build(self.app);self.assertTrue(r['complete'])
        self.assertEqual(len(searching.search(self.app,'控制')['rows']),1)
        self.assertEqual(len(searching.search(self.app,'稳定性分析')['rows']),1)
        self.assertEqual(searching.search(self.app,'" OR *')['rows'],[]);self.assertEqual(before,p.read_bytes())
    def test_incremental_index_updates_deletes_and_resumes(self):
        a=self.file('00_收件箱/a.txt','course alpha');b=self.file('00_收件箱/b.txt','course beta')
        first=searching.build(self.app,budget=1);self.assertFalse(first['complete']);self.assertEqual(first['remaining'],1)
        second=searching.build(self.app);self.assertEqual(second['cached'],1)
        a.write_text('new changed',encoding='utf-8');b.unlink();r=searching.build(self.app)
        self.assertEqual(r['updated'],1);self.assertEqual(r['deleted'],1);self.assertEqual(searching.search(self.app,'beta')['rows'],[])
    def test_local_semantic_is_bounded_and_does_not_call_network(self):
        for name,text in [('control','control systems feedback stability'),('robot','robot control feedback sensors'),('poetry','poetry literature art')]:self.file('06_资料库/'+name+'.txt',text)
        searching.build(self.app)
        with patch('urllib.request.urlopen',side_effect=AssertionError('no network')):result=searching.search(self.app,'feedback control',True)
        self.assertEqual(result['mode'],'semantic_lsa');self.assertTrue(result['rows']);self.assertNotIn('poetry',result['rows'][0]['path'])
    def test_accepted_recognition_is_content_bound_and_preserves_originals(self):
        import os
        from personalstorage import recognition
        from personalstorage.extraction import extract
        p=self.file('00_收件箱/scan.dat','opaque-source')
        with patch.dict(os.environ,{'PERSONALSTORAGE_HOME':str(self.base/'machine')}):
            before=p.read_bytes();recognition.accept(p,{'text':'verified course text','source':'local_audio','complete':False})
            self.assertEqual(extract(p)['text'],'verified course text');p.rename(p.with_name('renamed.dat'));p=p.with_name('renamed.dat')
            self.assertEqual(extract(p)['name'],'renamed.dat');self.assertEqual(p.read_bytes(),before)
            p.write_text('other--source',encoding='utf-8');self.assertFalse(extract(p).get('recognition_accepted',False))
    def test_index_cancellation_keeps_originals(self):
        p=self.file('00_收件箱/x.txt','course');event=threading.Event();event.set()
        with scope(event),self.assertRaises(Cancelled):searching.build(self.app)
        self.assertTrue(p.exists())
    def test_owned_helper_cancel_finishes_without_waiting_for_sleep(self):
        import sys,time,subprocess
        from personalstorage.processes import run
        event=threading.Event();timer=threading.Timer(.3,event.set);timer.start();started=time.monotonic()
        try:
            with scope(event),self.assertRaises(Cancelled):run([sys.executable,'-c','import time;time.sleep(30)'],timeout=20,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.assertLess(time.monotonic()-started,3)
        finally:timer.cancel()
    def test_explicit_task_context_scan_and_plan_agree(self):
        self.cfg['tasks']=[{'id':'course','path':'01_课程学习/测试课程','lifecycle':'active','storage':'active'}];write_json(self.root/'_整理规则/分类配置.json',self.cfg);self.app.policy=Policy(self.root)
        p=self.file('00_收件箱/new.txt','unrelated body');context={'task_id':'course','purpose':'专用实验资料'}
        rows=scanning.scan(self.root,self.app.policy,{},stable_seconds=0,context=context)['rows'];self.assertEqual(rows[0]['rule_id'],'context_task')
        plan=self.app.plan(sources=['00_收件箱/new.txt'],context=context,stable_seconds=0);self.assertEqual(plan['actions'][0]['target'],rows[0]['target']);self.assertTrue(p.exists())
    def test_management_restore_preview_commit_preserves_originals(self):
        p=self.file('00_收件箱/x.txt','course');record=backup.create_management(self.app);self.app.state.set('sentinel','changed')
        preview=backup.restore_preview(self.app,record['path']);self.app.state.set('automation',{'x':1});r=backup.restore(self.app,preview)
        self.assertTrue(r['restored']);self.assertIsNone(self.app.state.get('sentinel'));self.assertIsNone(self.app.state.get('automation'));self.assertEqual(p.read_text(),'course')
    def test_restore_rejects_wrong_identity_and_zip_slip(self):
        path=self.base/'bad.zip'
        with zipfile.ZipFile(path,'w') as archive:archive.writestr('../outside.txt','bad')
        with self.assertRaises(SafetyError):backup.restore_preview(self.app,path)
        self.assertFalse((self.base/'outside.txt').exists())
    def test_original_backup_and_restore_to_new_folder_never_overwrite(self):
        p=self.file('01_课程学习/测试课程/a.txt','original source')
        plan=backup.original_preview(self.app,self.base/'backup-destination');record=backup.backup_originals(self.app,plan)
        destination=self.base/'restored';ticket=backup.original_restore_preview(self.app,record['id'],destination);backup.restore_originals(self.app,ticket)
        self.assertEqual((destination/'01_课程学习/测试课程/a.txt').read_bytes(),p.read_bytes())
        with self.assertRaises(SafetyError):backup.original_restore_preview(self.app,record['id'],destination)
    def test_original_backup_source_change_and_disk_full_abort(self):
        p=self.file('00_收件箱/x.txt','one');ticket=backup.original_preview(self.app,self.base/'dest');p.write_text('changed')
        with self.assertRaises(SafetyError):backup.backup_originals(self.app,ticket)
        usage=type('Usage',(),{'free':1})()
        with patch('shutil.disk_usage',return_value=usage),self.assertRaises(SafetyError):backup.original_preview(self.app,self.base/'next')
    def test_lifecycle_preview_no_change_then_rename_updates_binding(self):
        self.cfg['tasks']=[{'id':'course','path':'01_课程学习/测试课程','lifecycle':'active','storage':'active'}];write_json(self.root/'_整理规则/分类配置.json',self.cfg);self.app.policy=Policy(self.root)
        p=self.file('01_课程学习/测试课程/source.txt','unchanged bytes');before=manifest(p.parent)
        ticket=lifecycle.preview(self.app,'course','completed');self.assertTrue(p.exists())
        result=lifecycle.apply(self.app,ticket);target=self.root/result['target'];self.assertEqual(before,manifest(target));self.assertEqual(self.app.policy.data['tasks'][0]['path'],result['target'])
        self.assertEqual(self.app.policy.data['executable_rules'][0]['target_parent'],result['target'])
    def test_ppt_record_parser_and_doc_piece_table(self):
        raw='合成PPT course'.encode('utf-16le');record=struct.pack('<HHI',0,4000,len(raw))+raw
        self.assertIn('合成PPT',ppt_text(record,100))
        word=bytearray(1000);struct.pack_into('<H',word,0,0xa5ec);text='课程合成文本';encoded=text.encode('utf-16le');word[600:600+len(encoded)]=encoded
        piece=struct.pack('<II',0,len(text))+b'\0\0'+struct.pack('<I',600)+b'\0\0';clx=b'\x02'+struct.pack('<I',len(piece))+piece;struct.pack_into('<II',word,418,0,len(clx))
        self.assertEqual(doc_text(word,clx,100),text)
    def test_html_rtf_open_format_read_only(self):
        p=self.file('00_收件箱/page.html','<p>course</p><script>untrusted command</script><table><tr><td>value</td></tr></table>')
        text=read(p)['text'];self.assertIn('course',text);self.assertNotIn('untrusted',text)
        rtf=self.file('00_收件箱/note.rtf',r'{\rtf1\ansi course \b reference\b0}')
        self.assertIn('course',read(rtf)['text'])
    def test_ai_candidates_only_after_explicit_prepare_and_remain_unpublished(self):
        from personalstorage.ai import AIManager
        from personalstorage import assistance
        from test_ai import FakeVault,FakeProvider
        provider=FakeProvider();manager=AIManager(self.app,FakeVault(),lambda *a,**k:provider);manager.configure({'enabled':True})
        before=self.app.policy.hash;packet=assistance.prepare(self.app,manager,'rules','Create a course keyword rule')
        self.assertEqual(provider.calls,0);result=assistance.run(self.app,manager,packet)
        self.assertFalse(result['rules'][0]['enabled']);self.assertEqual(Policy(self.root).hash,before);self.assertEqual(manager.status()['calls_today'],1)
        provider.bad_target=True;packet=assistance.prepare(self.app,manager,'rules','Create a course keyword rule')
        with self.assertRaises(SafetyError):assistance.run(self.app,manager,packet)
        self.assertEqual(manager.status()['calls_today'],2)


class InstallerTests(unittest.TestCase):
    def test_install_uninstall_preserves_data_and_modified_files(self):
        from tools.setup_entry import install,uninstall,MAGIC
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);payload=base/'payload.zip'
            with zipfile.ZipFile(payload,'w') as z:z.writestr('PersonalStorage/PersonalStorage.exe',b'synthetic exe');z.writestr('PersonalStorage/docs/guide.txt','guide')
            meta=base/'payload.json';meta.write_text(json.dumps({'version':'0.10.0b1','prerelease':True,'payload_sha256':hashlib.sha256(payload.read_bytes()).hexdigest()}))
            target=base/'program';data=base/'data/original.txt';data.parent.mkdir();data.write_text('keep')
            install(target,payload,meta,register=False);(target/'0.10.0b1/docs/guide.txt').write_text('local note')
            result=uninstall(target,register=False);self.assertTrue(result['modified_program_files_preserved']);self.assertEqual(data.read_text(),'keep');self.assertTrue((target/'0.10.0b1/docs/guide.txt').exists())
    def test_installer_payload_tampering_and_traversal_rejected(self):
        from tools.setup_entry import install
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);payload=base/'payload.zip'
            with zipfile.ZipFile(payload,'w') as z:z.writestr('PersonalStorage/../outside.txt','bad')
            meta=base/'m.json';meta.write_text(json.dumps({'version':'0.10.0b1','prerelease':True,'payload_sha256':hashlib.sha256(payload.read_bytes()).hexdigest()}))
            with self.assertRaises(ValueError):install(base/'program',payload,meta,register=False)
            self.assertFalse((base/'outside.txt').exists())
    def test_release_gate_refuses_one_point_zero_without_signoff(self):
        import importlib.util
        path=Path(__file__).resolve().parents[1]/'tools/release_gate.py';spec=importlib.util.spec_from_file_location('test_release_gate',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.validate(),'0.10.0b1')


if __name__=='__main__':unittest.main()
