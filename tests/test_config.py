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
        self.file_patch=patch('personalstorage.config.__file__',str(self.base/'app/personalstorage/config.py'))
        self.file_patch.start()
        self.env_patch=patch.dict(os.environ,{},clear=False);self.env_patch.start()
        os.environ.pop('PERSONALSTORAGE_ROOT',None)

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
