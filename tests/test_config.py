import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personalstorage.common import SafetyError, read_json, write_json
from personalstorage.config import default_root
from personalstorage.setup import setup_library
from personalstorage.policy import Policy


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.file_patch=patch('personalstorage.config.program_home',return_value=self.base/'app')
        self.file_patch.start()
        self.env_patch=patch.dict(os.environ,{},clear=False);self.env_patch.start()
        os.environ.pop('PERSONALSTORAGE_ROOT',None)
        os.environ['PERSONALSTORAGE_HOME']=str(self.base/'runtime')

    def tearDown(self):
        self.env_patch.stop();self.file_patch.stop();self.temp.cleanup()

    def test_default_name(self):
        self.assertEqual(default_root(),r'D:\PersonalStorage')

    def test_environment_overrides_local_config(self):
        write_json(self.base/'app/.local/config.json',{'root':str(self.base/'local')})
        os.environ['PERSONALSTORAGE_ROOT']=str(self.base/'override')
        self.assertEqual(default_root(),str(self.base/'override'))

    def test_local_config_and_relative_rejection(self):
        target=self.base/'app/.local/config.json'
        write_json(target,{'root':str(self.base/'local')})
        self.assertEqual(default_root(),str(self.base/'local'))
        write_json(target,{'root':'relative/library'})
        with self.assertRaises(SafetyError):default_root()

    def test_setup_is_empty_and_policy_valid(self):
        root=self.base/'data'
        result=setup_library(root)
        self.assertFalse(result['original_files_touched'])
        self.assertEqual(Policy(root).data['executable_rules'],[])
        self.assertEqual(Policy(root).data['tasks'],[])
        self.assertEqual(list((root/'00_收件箱/待确认').iterdir()),[])

    def test_setup_does_not_overwrite_existing_rules_or_files(self):
        root=self.base/'data';setup_library(root)
        source=root/'00_收件箱/example.txt';source.write_text('private local content')
        before=(root/'_整理规则/分类配置.json').read_bytes()
        with self.assertRaises(SafetyError):setup_library(root)
        self.assertEqual((root/'_整理规则/分类配置.json').read_bytes(),before)
        self.assertEqual(source.read_text(),'private local content')

    def test_setup_rejects_code_repository(self):
        from personalstorage import setup
        repo=Path(setup.__file__).resolve().parents[1]
        with self.assertRaises(SafetyError):setup_library(repo)

    def test_setup_builds_fixed_structure_without_dynamic_tasks(self):
        root=self.base/'data';result=setup_library(root)
        config=read_json(root/'_整理规则/分类配置.json')
        self.assertEqual(len(config['categories']),11)
        self.assertEqual(len(result['fixed_subdirectories']),12)
        for relative in config['initialization']['fixed_subdirectories']:
            self.assertTrue((root/relative).is_dir(),relative)
        for name in ('01_课程学习','02_项目','03_竞赛'):
            self.assertEqual(list((root/name).iterdir()),[])
        self.assertFalse(any('<' in p.name or '>' in p.name for p in root.rglob('*')))
        self.assertIn('细分目录规则',(root/'_整理规则/通用分类规则.md').read_text(encoding='utf-8'))
        self.assertTrue(config['classification_rules'])
        self.assertTrue(config['subcategory_rules'])
        self.assertEqual(Policy(root).classify({'name':'lecture.pdf','kind':'file','text':'通用机器学习教材'})['decision'],'review')

    def test_invalid_skeleton_rejected_before_creation(self):
        import copy
        from personalstorage import setup
        template=read_json(Path(setup.__file__).with_name('templates')/'classification-config.json')
        for relative in ('../outside','10_未批准/资料','02_项目/<项目名>'):
            root=self.base/str(len(relative))
            config=copy.deepcopy(template)
            config['initialization']['fixed_subdirectories']=[relative]
            with patch('personalstorage.setup.read_json',return_value=config):
                with self.assertRaises(SafetyError):setup_library(root)
            self.assertFalse(root.exists())

    def test_machine_root_survives_program_folder_change(self):
        from personalstorage.config import save_root
        root=self.base/'data';setup_library(root);save_root(root)
        with patch('personalstorage.config.program_home',return_value=self.base/'different-program'):
            self.assertEqual(default_root(),str(root))
        os.environ['PERSONALSTORAGE_ROOT']=str(self.base/'explicit')
        self.assertEqual(default_root(),str(self.base/'explicit'))

    def test_machine_state_home_is_separate_from_library(self):
        from personalstorage.paths import runtime_home
        from personalstorage.state import State
        root=self.base/'data';setup_library(root)
        state=State(root)
        try:self.assertTrue(state.profile.is_relative_to(runtime_home()));self.assertFalse(state.profile.is_relative_to(root))
        finally:state.close()

    def test_dot_segments_keep_same_library_identity(self):
        from personalstorage.service import Application
        root=self.base/'data';setup_library(root)
        a=Application(root);a.close()
        b=Application(root/'00_收件箱'/ '..')
        try:self.assertEqual(b.root,root)
        finally:b.close()

    @unittest.skipUnless(os.name=='nt','Windows path case')
    def test_path_case_does_not_create_a_new_library_identity(self):
        from personalstorage.service import Application
        root=self.base/'data';setup_library(root)
        a=Application(root);identity=a.state.get('identity');a.close()
        b=Application(str(root).swapcase())
        try:self.assertEqual(b.state.get('identity'),identity)
        finally:b.close()
