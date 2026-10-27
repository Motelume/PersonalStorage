import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from personalstorage.common import SafetyError,digest,write_json,read_json
from personalstorage.service import Application
from personalstorage.rule_manager import RuleManager,KEY,model
from personalstorage.policy import Policy
from test_core import config


class RuleManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'library'
        (self.root/'01_课程学习/测试课程').mkdir(parents=True);(self.root/'00_收件箱').mkdir()
        self.path=self.root/'_整理规则/分类配置.json';write_json(self.path,config())
        self.original=self.root/'00_收件箱/note.txt';self.original.write_text('course',encoding='utf-8')
        self.app=Application(self.root,self.base/'state');self.manager=RuleManager(self.app)
    def tearDown(self):self.app.close();self.tmp.cleanup()
    def save(self,edit=None):
        status=self.manager.status();draft=status['draft'];data=copy.deepcopy(edit or (draft or {}).get('model') or status['live'])
        return self.manager.save(data,(draft or {}).get('revision'),status['policy_hash'])['draft']
    def simulate(self,revision,**features):
        return self.manager.simulate(revision,[{'features':{'name':'note.txt','text':'course','kind':'file',**features}}])
    def changed(self):
        data=self.manager.status()['live'];data['rules'][0]['rule']['all'][0]['value']='new.txt';return self.save(data)
    def test_draft_is_private_persistent_and_does_not_touch_effective_config(self):
        original=self.path.read_bytes();draft=self.changed()
        self.assertEqual(original,self.path.read_bytes());self.assertTrue(self.original.exists())
        self.app.close();self.app=Application(self.root,self.base/'state');self.manager=RuleManager(self.app)
        self.assertEqual(self.manager.status()['draft']['revision'],draft['revision'])
    def test_requires_test_then_publishes_and_revokes_authorization(self):
        d=self.changed();self.app.state.set('automation',{'policy_hash':self.app.policy.hash})
        with self.assertRaises(SafetyError):self.manager.preview(d['revision'])
        self.simulate(d['revision'],name='new.txt');preview=self.manager.preview(d['revision']);result=self.manager.publish(preview)
        self.assertTrue(result['effective_changed']);self.assertIsNone(self.app.state.get('automation'))
        self.assertEqual(Policy(self.root).data['executable_rules'][0]['all'][0]['value'],'new.txt')
        self.assertTrue(self.original.exists());self.assertIsNone(self.manager.status()['draft'])
        reading=self.root/'_整理规则/规则阅读版.md';self.assertIn(result['policy_hash'],reading.read_text(encoding='utf-8'))
    def test_disabled_rule_retained_but_not_executable(self):
        data=self.manager.status()['live'];data['rules'][0]['enabled']=False;d=self.save(data)
        self.simulate(d['revision']);self.manager.publish(self.manager.preview(d['revision']))
        self.assertEqual(Policy(self.root).data['executable_rules'],[])
        self.assertFalse(self.manager.status()['live']['rules'][0]['enabled'])
    def test_metadata_only_does_not_invalidate_policy_or_authorization(self):
        old=self.app.policy.hash;self.app.state.set('automation',{'policy_hash':old})
        data=self.manager.status()['live'];data['rules'][0]['title']='Readable title';d=self.save(data)
        self.assertIsNotNone(self.manager.preview(d['revision'])['metadata_changes'])
        result=self.manager.publish(self.manager.preview(d['revision']))
        self.assertFalse(result['effective_changed']);self.assertEqual(Policy(self.root).hash,old)
        self.assertEqual(self.app.state.get('automation')['policy_hash'],old)
    def test_same_level_conflicts_explained_by_shared_engine(self):
        (self.root/'02_项目/A').mkdir(parents=True)
        data=self.manager.status()['live'];second=copy.deepcopy(data['rules'][0]);second['rule']['id']='other';second['rule']['all']=[{'field':'text','op':'contains','value':'course'}];second['rule']['target_parent']='02_项目/A';data['rules'].append(second)
        d=self.save(data);r=self.simulate(d['revision'])['results'][0]['draft']
        self.assertEqual(r['result']['reason'],'同层规则指向不同目标');self.assertEqual(r['selected_level'],100)
        self.assertEqual(len([x for x in r['rules'] if x['matched']]),2)
    def test_lower_level_wins_even_when_later_in_list(self):
        data=self.manager.status()['live'];second=copy.deepcopy(data['rules'][0]);second['rule'].update(id='earlier',level=10);data['rules'].append(second)
        d=self.save(data);r=self.simulate(d['revision'])['results'][0]['draft'];self.assertEqual(r['result']['rule_id'],'earlier')
    def test_duplicate_id_even_if_disabled_and_protections_are_rejected(self):
        data=self.manager.status()['live'];row=copy.deepcopy(data['rules'][0]);row['enabled']=False;data['rules'].append(row)
        self.assertFalse(self.manager.validate(data)['valid']);data=self.manager.status()['live'];data['config']['constraints']['no_delete']=False
        self.assertFalse(self.manager.validate(data)['valid'])
    def test_stale_draft_and_publish_preview_are_rejected(self):
        d=self.save();updated=copy.deepcopy(d['model']);updated['rules'][0]['note']='newer'
        self.manager.save(updated,d['revision'],d['base_hash'])
        with self.assertRaises(SafetyError):self.manager.save(updated,d['revision'],d['base_hash'])
        with self.assertRaises(SafetyError):self.manager.publish({'revision':d['revision'],'policy_hash':d['base_hash'],'model_hash':digest(d['model'])})
    def test_external_change_rebase_preserves_both_independent_changes(self):
        d=self.save();edited=copy.deepcopy(d['model']);edited['rules'][0]['title']='My name';self.save(edited)
        external=read_json(self.path);external['description']='external';write_json(self.path,external)
        draft=self.manager.status()['draft'];result=self.manager.rebase(draft['revision'])
        self.assertEqual(result['conflicts'],[]);self.assertEqual(result['draft']['model']['config']['description'],'external')
        self.assertEqual(result['draft']['model']['rules'][0]['title'],'My name')
    def test_rebase_conflict_requires_explicit_field_choice(self):
        d=self.changed();external=read_json(self.path);external['executable_rules'][0]['all'][0]['value']='external.txt';write_json(self.path,external)
        result=self.manager.rebase(d['revision']);self.assertTrue(result['conflicts'])
        self.assertEqual(self.manager.status()['draft']['revision'],d['revision'])
        result=self.manager.rebase(d['revision'],{x['field']:'theirs' for x in result['conflicts']})
        self.assertEqual(result['draft']['model']['rules'][0]['rule']['all'][0]['value'],'external.txt')
    def test_real_sample_changes_invalidate_publish_test_and_no_review_is_created(self):
        d=self.changed();self.manager.simulate(d['revision'],[{'path':'00_收件箱/note.txt'}]);self.original.write_text('changed')
        with self.assertRaises(SafetyError):self.manager.preview(d['revision'])
        self.assertEqual(self.app.state.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0],0)
    def test_reading_preserves_existing_private_markdown_and_non_generated_output(self):
        private=self.path.parent/'整理规则.md';private.write_text('my private notes',encoding='utf-8')
        out=self.path.parent/'规则阅读版.md';out.write_text('unrelated original file',encoding='utf-8')
        with self.assertRaises(SafetyError):self.manager.reading()
        self.assertEqual(private.read_text(),'my private notes');self.assertEqual(out.read_text(),'unrelated original file')
    def test_history_rollback_creates_draft_not_file_rollback(self):
        d=self.changed();self.simulate(d['revision']);self.manager.publish(self.manager.preview(d['revision']))
        old=self.manager.status()['history'][-1];self.manager.reset(None,old['id'])
        self.assertEqual(Policy(self.root).data['executable_rules'][0]['all'][0]['value'],'new.txt')
        self.assertEqual(self.manager.status()['draft']['model']['rules'][0]['rule']['all'][0]['value'],'note.txt');self.assertTrue(self.original.exists())
    def test_startup_recovers_committed_json_and_matching_metadata(self):
        d=self.changed();self.simulate(d['revision']);original_set=self.app.state.set
        def fail(key,value):
            if key==KEY:raise OSError('simulated crash after config commit')
            return original_set(key,value)
        with patch.object(self.app.state,'set',side_effect=fail):
            with self.assertRaises(OSError):self.manager.publish(self.manager.preview(d['revision']))
        self.app.close();self.app=Application(self.root,self.base/'state');self.manager=RuleManager(self.app)
        self.assertIsNone(self.app.state.get('rule_publish_journal'));self.assertIsNone(self.manager.status()['draft'])
        self.assertEqual(self.app.policy.data['executable_rules'][0]['all'][0]['value'],'new.txt')
    def test_failure_before_config_commit_keeps_old_config(self):
        d=self.changed();self.simulate(d['revision']);original=self.path.read_bytes()
        with patch('personalstorage.rule_manager.write_json',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.manager.publish(self.manager.preview(d['revision']))
        self.assertEqual(original,self.path.read_bytes())
    def test_missing_context_never_matches_empty_value(self):
        self.assertFalse(Policy.matches({'field':'context.purpose','op':'equals','value':''},{}))
    def test_regex_timeout_cannot_hang_classification(self):
        p=self.app.policy;data=copy.deepcopy(p.data);data['executable_rules'][0]['all']=[{'field':'text','op':'regex','value':'(a+)+$'}]
        result=Policy.from_data(self.root,data).classify({'text':'a'*200+'!'})
        self.assertEqual(result['decision'],'review');self.assertIn('超时',result['reason'])
    def test_semantic_preferences_feed_same_effective_config_and_reading(self):
        data=self.manager.status()['live'];data['config']['explicit_preferences']=[{'id':'pref','description':'Task-specific references remain in their task.'}]
        d=self.save(data);self.manager.publish(self.manager.preview(d['revision']))
        self.assertIn('Task-specific',read_json(self.path)['explicit_preferences'][0]['description'])
        self.assertIn('Task-specific',(self.path.parent/'规则阅读版.md').read_text(encoding='utf-8'))


if __name__=='__main__':unittest.main()
