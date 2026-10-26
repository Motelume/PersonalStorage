import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from personalstorage.common import SafetyError
from personalstorage.common import write_json,read_json
from personalstorage.rule_package import preview,update,template
from personalstorage.service import Application
from personalstorage.setup import setup_library
from test_core import config


class SharedRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.root=self.base/'library'
        self.root.mkdir();self.data=config()
        self.data['tasks']=[{'id':'local-task','path':'02_项目/本机任务','storage':'active','lifecycle':'active'}]
        self.data['explicit_preferences']=[{'meaning':'local preference'}]
        (self.root/'02_项目/本机任务').mkdir(parents=True)
        self.original=self.root/'02_项目/本机任务/private.txt';self.original.write_text('private example')
        write_json(self.root/'_整理规则/分类配置.json',self.data)
        self.local=self.root/'_整理规则/整理规则.md';self.local.write_text('本机私人约定',encoding='utf-8')
        self.app=Application(self.root,self.base/'profile')
    def tearDown(self):self.app.close();self.temp.cleanup()

    def test_preview_is_read_only_and_apply_preserves_private_rules(self):
        before=(self.root/'_整理规则/分类配置.json').read_bytes()
        plan=preview(self.app)
        self.assertTrue(plan['add_fields']);self.assertEqual((self.root/'_整理规则/分类配置.json').read_bytes(),before)
        result=update(self.app);data=read_json(self.root/'_整理规则/分类配置.json')
        self.assertTrue(Path(result['backup']).is_dir())
        for field in ('tasks','executable_rules','explicit_preferences','categories','constraints'):
            self.assertEqual(data[field],self.data[field])
        self.assertEqual(self.local.read_text(encoding='utf-8'),'本机私人约定')
        self.assertEqual(self.original.read_text(),'private example')
        self.assertTrue((self.root/'_整理规则/通用分类规则.md').is_file())

    def test_repeated_update_is_idempotent(self):
        update(self.app);self.app.close();self.app=Application(self.root,self.base/'profile')
        self.assertFalse(update(self.app)['updated'])

    def test_user_edited_shared_document_is_preserved(self):
        update(self.app);self.app.close();self.app=Application(self.root,self.base/'profile')
        doc=self.root/'_整理规则/通用分类规则.md';doc.write_text('用户额外说明',encoding='utf-8')
        result=update(self.app)
        self.assertEqual(result['document_action'],'preserve_local_edits')
        self.assertEqual(doc.read_text(encoding='utf-8'),'用户额外说明')

    def test_existing_semantic_rule_overrides_preserved(self):
        self.data['classification_rules']=[{'id':'custom','action':'local custom'}]
        write_json(self.root/'_整理规则/分类配置.json',self.data)
        self.app.close();self.app=Application(self.root,self.base/'profile')
        update(self.app)
        self.assertEqual(read_json(self.root/'_整理规则/分类配置.json')['classification_rules'],self.data['classification_rules'])

    def test_fresh_setup_records_shared_version_without_private_data(self):
        other=self.base/'new-library';setup_library(other)
        meta=read_json(other/'_整理规则/通用规则版本.json')
        self.assertEqual(meta['package'],template()['shared_package'])
        self.assertNotIn('library_root',meta)
        self.assertIn('本机个人分类规则',(other/'_整理规则/整理规则.md').read_text(encoding='utf-8'))

    def test_new_version_updates_only_unmodified_shared_fields(self):
        import copy
        update(self.app);self.app.close();self.app=Application(self.root,self.base/'profile')
        newer=copy.deepcopy(template());newer['shared_package']['version']='1.3.0'
        newer['evidence_rules']['new_shared_guidance']='new generic guidance'
        with patch('personalstorage.rule_package.template',return_value=newer):
            plan=preview(self.app)
            self.assertIn('evidence_rules',plan['update_unmodified_fields'])
            update(self.app)
        current=read_json(self.root/'_整理规则/分类配置.json')
        self.assertIn('new_shared_guidance',current['evidence_rules'])
        self.assertEqual(current['tasks'],self.data['tasks'])
        self.assertEqual(current['executable_rules'],self.data['executable_rules'])

    def test_incompatible_schema_refuses_update(self):
        import copy
        before=(self.root/'_整理规则/分类配置.json').read_bytes()
        incompatible=copy.deepcopy(template());incompatible['shared_package']['compatible_schema']='4.0'
        with patch('personalstorage.rule_package.template',return_value=incompatible):
            with self.assertRaises(SafetyError):update(self.app)
        self.assertEqual((self.root/'_整理规则/分类配置.json').read_bytes(),before)
