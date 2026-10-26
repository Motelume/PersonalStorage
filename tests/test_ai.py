import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from personalstorage.ai import AIManager
from personalstorage.common import SafetyError, now, write_json, manifest, digest
from personalstorage.credentials import CredentialVault
from personalstorage.model_client import DeepSeekProvider, ProviderError, BASE_URL
from personalstorage.service import Application
from personalstorage.web import make_server
from test_core import config


class FakeVault:
    def configured(self):return True
    def read(self):return 'test-only-credential'


class FakeProvider:
    def __init__(self,mutate=None,decision='suggest_reclassify',bad_target=False):
        self.usage={'prompt_tokens':40,'completion_tokens':20,'total_tokens':60}
        self.mutate=mutate;self.decision=decision;self.bad_target=bad_target;self.calls=0
    def test(self):return {'connected':True,'models':['deepseek-flash']}
    def review(self,request):
        self.calls+=1
        if self.mutate:self.mutate()
        target=next(t['id'] for t in request['candidate_targets'] if t['path']=='01_课程学习/测试课程')
        return {'items':[{'id':d['id'],'decision':self.decision,'target_id':'unknown' if self.bad_target else target,'reason':'此资料属于已确认课程，建议归入现有课程目录'} for d in request['documents']]}


class AITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.root=self.base/'library'
        (self.root/'00_收件箱').mkdir(parents=True);(self.root/'01_课程学习/测试课程').mkdir(parents=True)
        write_json(self.root/'_整理规则/分类配置.json',config())
        self.app=Application(self.root,self.base/'profile')
        self.file=self.root/'00_收件箱/unknown.txt';self.file.write_text('示例课程资料',encoding='utf-8')
        self.provider=FakeProvider()
        self.manager=AIManager(self.app,FakeVault(),lambda *a,**k:self.provider)
        self.manager.configure({'enabled':True})
    def tearDown(self):self.app.close();self.temp.cleanup()
    def packet(self):
        action=self.app.plan(stable_seconds=0)['actions'][0]
        return self.manager.prepare([{'path':action['source'],'action':action}])

    def test_ai_suggests_without_moving(self):
        before=manifest(self.file);result=self.manager.run(self.packet())
        self.assertFalse(result['files_moved']);self.assertEqual(manifest(self.file),before)
        self.assertEqual(self.app.pending()[0]['status'],'correction_required')
        self.assertEqual(self.app.pending()[0]['details']['result']['target_parent'],'01_课程学习/测试课程')
        self.assertEqual(self.manager.status()['tokens_recorded'],60)

    def test_metadata_only_and_disabled(self):
        self.manager.configure({'with_content':False})
        packet=self.packet();self.assertEqual(packet['request']['documents'][0]['text'],'')
        self.manager.configure({'enabled':False})
        with self.assertRaises(SafetyError):self.manager.run(packet)
        self.assertEqual(self.provider.calls,0)

    def test_stale_source_not_sent(self):
        packet=self.packet();self.file.write_text('different')
        with self.assertRaises(SafetyError):self.manager.run(packet)
        self.assertEqual(self.provider.calls,0)

    def test_content_changes_during_review_rejected(self):
        packet=self.packet();self.provider.mutate=lambda:self.file.write_text('changed during request')
        with self.assertRaises(SafetyError):self.manager.run(packet)
        self.assertEqual(self.app.pending(),[])
        self.assertEqual(self.manager.status()['history'][0]['status'],'failed')

    def test_rule_change_during_review_rejected(self):
        packet=self.packet()
        def change():
            data=config();data['policy_version']='changed';write_json(self.root/'_整理规则/分类配置.json',data)
        self.provider.mutate=change
        with self.assertRaises(SafetyError):self.manager.run(packet)
        self.assertEqual(self.app.pending(),[])

    def test_unlisted_target_and_missing_item_rejected(self):
        self.provider.bad_target=True
        with self.assertRaises(SafetyError):self.manager.run(self.packet())
        self.assertEqual(self.app.pending(),[])
        self.provider.review=lambda request:{'items':[]}
        with self.assertRaises(SafetyError):self.manager.run(self.packet())

    def test_confirm_unclassified_requires_user(self):
        self.provider.decision='confirm';result=self.manager.run(self.packet())
        self.assertEqual(result['results'][0]['status'],'waiting_user')
        self.assertTrue(self.file.exists())

    def test_manual_lock_preserved(self):
        m=manifest(self.file)
        plan=self.app.plan(sources=['00_收件箱/unknown.txt'],stable_seconds=0,
            decisions={'00_收件箱/unknown.txt':{'manifest_hash':digest(m),'target_parent':'01_课程学习/测试课程','evidence':'用户决定'}},approve_decisions=True)
        self.app.execute(plan['id']);pending=self.app.pending()[0]
        packet=self.manager.prepare([{'path':pending['path'],'review_id':pending['review_id']}])
        result=self.manager.run(packet)
        self.assertEqual(result['results'][0]['status'],'waiting_user')
        self.assertTrue((self.root/pending['path']).exists())

    def test_daily_limit_prevents_request(self):
        self.manager.configure({'daily_limit':1})
        self.manager.run(self.packet())
        with self.assertRaises(SafetyError):self.manager.run(self.packet())
        self.assertEqual(self.provider.calls,1)

    def test_only_safe_bounded_existing_directories(self):
        (self.root/'01_课程学习/已结束（已归档）').mkdir()
        targets=self.manager.targets()
        self.assertFalse(any('已归档' in t['path'] or t['path'].startswith('09_') for t in targets))
        self.assertLessEqual(len(targets),400)


@unittest.skipUnless(os.name=='nt','Windows DPAPI')
class VaultTests(unittest.TestCase):
    def test_encrypted_key_roundtrip_and_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            vault=CredentialVault(Path(folder)/'secrets.sqlite3');key='sk-'+'testcredential'*3
            self.assertFalse(vault.configured());vault.save(key)
            self.assertTrue(vault.configured());self.assertEqual(vault.read(),key)
            self.assertNotIn(key.encode(),vault.path.read_bytes())
            vault.clear();self.assertFalse(vault.configured())


class ProviderTests(unittest.TestCase):
    def test_official_endpoint_and_json_contract(self):
        captured=[]
        class Opener:
            def open(self,request,timeout):
                captured.append(request)
                return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"items":[]}'}}],'usage':{'total_tokens':7}}).encode())
        provider=DeepSeekProvider('test-value',opener=Opener())
        self.assertEqual(provider.review({'documents':[]}),{'items':[]})
        self.assertEqual(captured[0].full_url,BASE_URL+'/chat/completions')
        payload=json.loads(captured[0].data)
        self.assertEqual(payload['response_format'],{'type':'json_object'})
        self.assertEqual(payload['thinking'],{'type':'disabled'})
        self.assertEqual(provider.usage['total_tokens'],7)

    def test_error_response_does_not_leak_key(self):
        from urllib.error import HTTPError
        key='do-not-expose-this-value'
        class Opener:
            def open(self,request,timeout):raise HTTPError(request.full_url,401,key,{},io.BytesIO(key.encode()))
        with self.assertRaises(ProviderError) as error:DeepSeekProvider(key,opener=Opener()).test()
        self.assertNotIn(key,str(error.exception))

    def test_malformed_or_truncated_json_rejected(self):
        class Opener:
            def open(self,request,timeout):return io.BytesIO(json.dumps({'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}).encode())
        with self.assertRaises(ProviderError):DeepSeekProvider('test',opener=Opener()).review({})
