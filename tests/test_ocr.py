import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from personalstorage import ocr,extraction,scanning
from personalstorage.common import write_json,SafetyError
from personalstorage.policy import Policy
from test_core import config

AVAILABLE=all(importlib.util.find_spec(n) is not None for n in ('PIL','pypdfium2','rapidocr','onnxruntime','pypdf'))

def sample_image(path,chinese=False):
    from PIL import Image,ImageDraw,ImageFont
    image=Image.new('RGB',(1500,520),'white');draw=ImageDraw.Draw(image)
    fonts=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts'
    font=ImageFont.truetype(str(fonts/('msyh.ttc' if chinese and (fonts/'msyh.ttc').exists() else 'arial.ttf')),52)
    draw.text((60,65),'课程资料：信号与系统' if chinese else 'course notes / PersonalStorage',font=font,fill='black')
    draw.text((60,170),'course notes / PersonalStorage',font=font,fill='black')
    image.save(path);image.close()

def native_pdf(path,text='course notes / native PDF text'):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=650,height=240)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    resources=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    page[NameObject('/Resources')]=resources
    stream=DecodedStreamObject();stream.set_data(('BT /F1 28 Tf 45 120 Td ('+text+') Tj ET').encode())
    page[NameObject('/Contents')]=writer._add_object(stream)
    with path.open('wb') as output:writer.write(output)

@unittest.skipUnless(AVAILABLE,'Install requirements-ocr.txt for real offline OCR tests')
class OCRTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='storage-ocr-test-');self.base=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'PERSONALSTORAGE_HOME':str(self.base/'machine')});self.env.start()
        self.image=self.base/'sample.png';sample_image(self.image)
    def tearDown(self):self.env.stop();self.tmp.cleanup()

    def test_real_image_recognition_cache_and_rename(self):
        before=self.image.read_bytes();first=extraction.extract(self.image)
        self.assertIn('course',first['text'],first);self.assertEqual(first['text_source'],'local_ocr');self.assertFalse(first['ocr']['cache_hit'])
        again=extraction.extract(self.image);self.assertTrue(again['ocr']['cache_hit']);self.assertEqual(first['text'],again['text'])
        copied=self.base/'renamed.png';shutil.copy2(self.image,copied)
        renamed=extraction.extract(copied);self.assertTrue(renamed['ocr']['cache_hit']);self.assertEqual(renamed['name'],'renamed.png');self.assertEqual(self.image.read_bytes(),before)

    def test_real_chinese_and_english(self):
        sample_image(self.image,True);result=extraction.extract(self.image)
        self.assertIn('信号',result['text'],result);self.assertIn('course',result['text'])

    def test_text_pdf_does_not_call_ocr(self):
        path=self.base/'native.pdf';native_pdf(path)
        with patch.object(ocr,'engine',side_effect=AssertionError('Native PDF must skip OCR')):result=ocr.read_document(path)
        self.assertIn('course',result['text']);self.assertEqual(result['text_source'],'native');self.assertEqual(result['ocr']['recognized_pages'],0);self.assertTrue(result['complete'])

    def mixed(self):
        from PIL import Image
        from pypdf import PdfWriter
        native=self.base/'native.pdf';scan=self.base/'scan.pdf';mixed=self.base/'mixed.pdf';native_pdf(native)
        with Image.open(self.image) as image:image.convert('RGB').save(scan,'PDF')
        writer=PdfWriter();writer.append(native);writer.append(scan)
        with mixed.open('wb') as output:writer.write(output)
        return mixed

    def test_real_mixed_pdf_and_page_budget(self):
        path=self.mixed();before=path.read_bytes();result=extraction.extract(path)
        self.assertEqual(result['text_source'],'mixed',result);self.assertEqual(result['ocr']['recognized_pages'],1)
        self.assertEqual([p['source'] for p in result['ocr']['pages']],['native','ocr']);self.assertEqual(result['ocr']['total_pages'],2)
        ocr.configure({'max_pages':1});short=extraction.extract(path)
        self.assertFalse(short['complete']);self.assertEqual(short['ocr']['processed_pages'],1);self.assertTrue(short['warnings']);self.assertEqual(path.read_bytes(),before)

    def test_low_confidence_text_is_not_used_for_rule_classification(self):
        output=SimpleNamespace(txts=['course uncertain','safe heading'],scores=[.6,.98])
        with patch.object(ocr,'engine',return_value=lambda *a,**k:output):result=ocr.read_document(self.image)
        self.assertNotIn('course',result['text']);self.assertIn('safe heading',result['text']);self.assertEqual(result['ocr']['low_confidence_lines'],1);self.assertFalse(result['complete'])

    def test_disabled_and_unavailable_are_explicit(self):
        with patch.object(ocr,'engine',side_effect=AssertionError('disabled')):
            disabled=ocr.read_document(self.image,options={**ocr.DEFAULTS,'enabled':False})
        self.assertEqual(disabled['ocr']['status'],'disabled');self.assertEqual(disabled['text'],'')
        with patch.object(ocr,'availability',return_value={'ready':False,'reason':'missing local models'}):result=ocr.read_document(self.image)
        self.assertEqual(result['ocr']['status'],'unavailable');self.assertFalse(result['complete'])

    def test_timeout_is_not_cached(self):
        with patch.object(extraction,'_worker',side_effect=subprocess.TimeoutExpired('synthetic',.01)):result=extraction.extract(self.image)
        self.assertEqual(result['ocr']['status'],'timeout');self.assertEqual(ocr.status()['cached_items'],0);self.assertTrue(self.image.exists())

    def test_content_change_invalidates_and_clear_invalidates_signature(self):
        first=extraction.extract(self.image);self.assertIn('course',first['text'])
        from PIL import Image
        with Image.new('RGB',(300,150),'white') as blank:blank.save(self.image)
        second=extraction.extract(self.image);self.assertFalse(second['ocr']['cache_hit']);self.assertEqual(second['text'],'')
        before=ocr.signature();ocr.clear_cache();self.assertEqual(ocr.status()['cached_items'],0);self.assertNotEqual(before,ocr.signature())

    def test_corrupt_image_and_encrypted_pdf_remain_unmodified(self):
        self.image.write_bytes(b'invalid image');result=extraction.extract(self.image);self.assertEqual(result['ocr']['status'],'error');self.assertEqual(self.image.read_bytes(),b'invalid image')
        from pypdf import PdfWriter
        encrypted=self.base/'encrypted.pdf';writer=PdfWriter();writer.add_blank_page(width=200,height=200);writer.encrypt('synthetic-password')
        with encrypted.open('wb') as output:writer.write(output)
        result=extraction.extract(encrypted);self.assertEqual(result['ocr']['status'],'error');self.assertEqual(result['text'],'')

    def test_settings_validation_and_offline_guard(self):
        for values in ({'max_pages':0},{'timeout':1000},{'min_confidence':float('nan')},{'enabled':'yes'}):
            with self.assertRaises(SafetyError):ocr.configure(values)
        with ocr.offline():
            with self.assertRaises(SafetyError):socket.create_connection(('example.com',443))

    def test_image_ocr_enters_existing_rule_scan(self):
        root=self.base/'library';(root/'00_收件箱').mkdir(parents=True);(root/'01_课程学习/测试课程').mkdir(parents=True)
        cfg=config();cfg['executable_rules'][0]['all']=[{'field':'text','op':'contains','value':'course'}]
        write_json(root/'_整理规则/分类配置.json',cfg);shutil.copy2(self.image,root/'00_收件箱/screenshot.png')
        result=scanning.scan(root,Policy(root),{},stable_seconds=0)
        self.assertEqual(result['rows'][0]['decision'],'move',result);self.assertEqual(result['rows'][0]['text_source'],'local_ocr');self.assertTrue((root/'00_收件箱/screenshot.png').exists())

    def test_cache_clear_epoch_rejects_inflight_result(self):
        _,epoch=ocr.cache_get('test-key');ocr.clear_cache();ocr.cache_put('test-key',{'text':'old result','ocr':{'status':'recognized'}},epoch)
        self.assertEqual(ocr.status()['cached_items'],0)

    def test_corrupt_cache_status_and_explicit_clear_recover(self):
        ocr.configure({'enabled':False});cache=self.base/'machine/ocr/cache.sqlite3';cache.write_bytes(b'not a sqlite database')
        self.assertIn('cache_warning',ocr.status());ocr.clear_cache()
        self.assertNotIn('cache_warning',ocr.status());self.assertEqual(ocr.status()['cached_items'],0)
        self.assertEqual(len(list(cache.parent.glob('cache-corrupt-*.sqlite3'))),1)

    def test_changed_original_during_worker_does_not_poison_cache(self):
        def changed(*args,**kwargs):
            self.image.write_bytes(b'new user content')
            return {'name':'source.png','text':'course from older snapshot','complete':True,'warnings':[],'ocr':{'status':'recognized'}}
        with patch.object(extraction,'_worker',side_effect=changed):result=extraction.extract(self.image)
        self.assertEqual(result['text'],'');self.assertEqual(result['ocr']['status'],'error');self.assertEqual(ocr.status()['cached_items'],0)

    def test_excessive_pixels_stop_before_decode_or_inference(self):
        class Huge:
            width=height=100000;n_frames=1
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def seek(self,index):pass
        with patch('PIL.Image.open',return_value=Huge()),patch.object(ocr,'engine',side_effect=AssertionError('must not infer')):result=ocr.read_document(self.image)
        self.assertEqual(result['ocr']['status'],'error');self.assertIn('像素',''.join(result['warnings']))

    def test_short_ai_excerpt_reuses_full_cached_result(self):
        full=extraction.extract(self.image);short=extraction.extract(self.image,limit=8)
        self.assertTrue(short['ocr']['cache_hit']);self.assertEqual(len(short['text']),8);self.assertFalse(short['complete'])
        restored=extraction.extract(self.image);self.assertEqual(restored['text'],full['text']);self.assertEqual(restored['complete'],full['complete'])
