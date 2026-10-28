import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from personalstorage.common import write_json,manifest,SafetyError
from personalstorage.service import Application
from personalstorage import quality_lab,searching
from personalstorage.ai import AIManager
from test_ai import FakeVault,FakeProvider
from test_core import config

class QualityLabTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();b=Path(self.tmp.name);self.root=b/'library';(self.root/'01_课程学习/测试课程').mkdir(parents=True);write_json(self.root/'_整理规则/分类配置.json',config());self.app=Application(self.root,b/'state');self.path=self.root/'01_课程学习/测试课程/lecture.txt';self.path.write_text('course lecture',encoding='utf-8');self.cases=[{'path':self.path.relative_to(self.root).as_posix(),'expected_target':'01_课程学习/测试课程'}];self.provider=FakeProvider();self.manager=AIManager(self.app,FakeVault(),lambda *a,**k:self.provider);self.manager.configure({'enabled':True})
 def tearDown(self):self.app.close();self.tmp.cleanup()
 def test_blind_comparison_does_not_modify_classifications(self):
  before=manifest(self.path);packet=quality_lab.prepare(self.app,self.manager,self.cases,'assistant_reviewed_reference');self.assertNotIn('expected_target',json.dumps(packet['documents']));self.assertEqual(packet['documents'][0]['source'],'lecture.txt');r=quality_lab.compare(self.app,self.manager,packet);self.assertEqual(r['ai_accuracy'],1);self.assertEqual(manifest(self.path),before);self.assertEqual(self.app.state.db.execute('SELECT COUNT(*) FROM assignments').fetchone()[0],0)
 def test_changed_source_blocks_before_api(self):
  packet=quality_lab.prepare(self.app,self.manager,self.cases,'user_supplied_labels');self.path.write_text('changed')
  with self.assertRaises(SafetyError):quality_lab.compare(self.app,self.manager,packet)
  self.assertEqual(self.provider.calls,0)
 def test_candidates_are_disabled_and_require_two_evidence_types(self):
  candidates=quality_lab.rule_candidates(self.app,self.cases);self.assertTrue(candidates);self.assertFalse(candidates[0]['enabled']);self.assertIn('all',candidates[0]['rule']['when']);self.assertEqual(len(self.app.policy.data['executable_rules']),1)
 def test_freshness_and_incomplete_walk_do_not_delete_old_records(self):
  searching.build(self.app);self.path.write_text('modified');other=self.path.with_name('new.txt');other.write_text('new');fresh=searching.freshness(self.app);self.assertEqual((fresh['new'],fresh['changed']),(1,1))
  def interrupted(root):
   yield other
   raise SafetyError('inaccessible directory')
  with patch('personalstorage.searching.files',interrupted):
   with self.assertRaises(SafetyError):searching.build(self.app)
  db=searching.database(self.app.state)
  try:self.assertIsNotNone(db.execute('SELECT path FROM documents WHERE path=?',(self.cases[0]['path'],)).fetchone())
  finally:db.close()
 def test_sampling_is_not_labelling(self):
  rows=quality_lab.sample(self.app,5)['samples'];self.assertTrue(rows);self.assertTrue(all(r['expected_target'] is None and r['label_status']=='unlabelled' for r in rows))
 def test_redirected_backup_preserves_atomic_publication(self):
  from personalstorage.rule_manager import RuleManager
  from personalstorage.common import write_json as real_write
  manager=RuleManager(self.app);s=manager.status();data=s['live'];data['rules'][0]['rule']['all'][1]['value']='new-course'
  draft=manager.save(data,None,s['policy_hash'])['draft'];manager.simulate(draft['revision'],[{'features':{'name':'note.txt','text':'new-course','kind':'file'}}]);preview=manager.preview(draft['revision'])
  def redirected(path,value):
   if Path(path).is_relative_to(self.app.state.profile):
    error=OSError('redirected folder');error.winerror=17;raise error
   return real_write(path,value)
  with patch('personalstorage.rule_manager.write_json',redirected):manager.publish(preview)
  self.assertTrue(list((self.root/'_整理规则/规则备份').rglob('classification-config.json')))
