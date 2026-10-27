import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
from personalstorage import activity,scanning
from personalstorage.common import now,write_json,manifest
from personalstorage.policy import Policy
from personalstorage.state import State
from test_core import config


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'library'
        (self.root/'00_收件箱').mkdir(parents=True)
        (self.root/'01_课程学习/测试课程').mkdir(parents=True)
        write_json(self.root/'_整理规则/分类配置.json',config())
        self.policy=Policy(self.root);self.state=State(self.root,self.base/'profile')
    def tearDown(self):self.state.close();self.tmp.cleanup()

    def test_scan_reads_no_hash_and_writes_no_plan(self):
        for i in range(12):(self.root/f'00_收件箱/{i}.txt').write_text('course')
        with patch('personalstorage.common.sha',side_effect=AssertionError('scan must not hash')),patch('personalstorage.scanning.time.sleep') as sleep:
            result=scanning.scan(self.root,self.policy,{})
        self.assertEqual(len(result['rows']),12);self.assertEqual(sleep.call_count,1)
        self.assertFalse(result['content_hashed']);self.assertFalse((self.root/'_整理规则/执行记录').exists())
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM plans').fetchone()[0],0)

    def test_scan_cache_invalidates_for_content_and_policy(self):
        p=self.root/'00_收件箱/note.txt';p.write_text('course')
        cache={}
        first=scanning.scan(self.root,self.policy,cache,stable_seconds=0)
        second=scanning.scan(self.root,self.policy,cache,stable_seconds=0)
        self.assertEqual(first['cache_hits'],0);self.assertEqual(second['cache_hits'],1)
        p.write_text('changed')
        third=scanning.scan(self.root,self.policy,cache,stable_seconds=0)
        self.assertEqual(third['cache_hits'],0)
        self.assertNotEqual(first['rows'][0]['scan_fingerprint'],third['rows'][0]['scan_fingerprint'])

    def test_change_during_observation_is_not_suggested(self):
        p=self.root/'00_收件箱/note.txt';p.write_text('course')
        with patch('personalstorage.scanning.time.sleep',side_effect=lambda _:p.write_text('changing')):
            result=scanning.scan(self.root,self.policy,{})
        self.assertEqual(result['rows'][0]['decision'],'skip')

    def test_scan_does_not_suggest_moving_management_instructions(self):
        (self.root/'00_收件箱/AGENTS.md').write_text('course')
        result=scanning.scan(self.root,self.policy,{},stable_seconds=0)
        self.assertEqual(result['rows'][0]['decision'],'skip')

    def test_history_survives_state_reopen_and_prunes_only_old_tasks(self):
        activity.begin(self.state,'recent','scan','inbox');activity.update(self.state,'recent','done',result={'rows':[{}]})
        activity.begin(self.state,'expired','ai-test','ai');activity.update(self.state,'expired','done')
        old=(datetime.fromisoformat(now())-timedelta(days=8)).isoformat(timespec='seconds')
        with self.state.db:
            self.state.db.execute('UPDATE ui_tasks SET started_at=?,finished_at=? WHERE id=?',(old,old,'expired'))
            self.state.db.execute('INSERT INTO operations VALUES(?,?,?,?,?)',('permanent','run','committed','{"source":"old.txt"}',old))
        self.state.close();self.state=State(self.root,self.base/'profile')
        activity.begin(self.state,'next','scan','inbox')
        result=activity.recent(self.state)
        self.assertIn('recent',[r['id'] for r in result['rows']]);self.assertNotIn('expired',[r['id'] for r in result['rows']])
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0],1)
        self.assertIsNone(self.state.db.execute('SELECT * FROM ui_tasks WHERE id="expired"').fetchone())
        self.assertNotIn('api_key',str(result))

    def test_history_paginates_and_filters_before_pagination(self):
        for i in range(105):activity.begin(self.state,str(i),'scan','inbox')
        first=activity.recent(self.state);second=activity.recent(self.state,offset=100)
        self.assertEqual(first['total'],105);self.assertEqual(len(first['rows']),100);self.assertEqual(len(second['rows']),5)
        self.assertEqual(activity.recent(self.state,query='不存在')['total'],0)
