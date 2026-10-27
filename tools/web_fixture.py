"""Isolated browser test data. Never points at the user's library."""
import sys
import os
import json
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tests'))
from test_core import config
from personalstorage.common import write_json
from personalstorage.web import make_server
from personalstorage.credentials import CredentialVault
from personalstorage.ai import AIManager

with tempfile.TemporaryDirectory(prefix='storage-ui-') as tmp:
    base=Path(tmp); root=base/'library'
    os.environ['PERSONALSTORAGE_HOME']=str(base/'machine')
    if '--ocr-fixture' in sys.argv:
        import os
        os.environ['PERSONALSTORAGE_HOME']=str(base/'machine')
    (root/'00_收件箱').mkdir(parents=True)
    (root/'01_课程学习/测试课程').mkdir(parents=True)
    c=config(); c['executable_rules'][0]['all']=[{'field':'text','op':'contains','value':'course'}]
    if '--beta-fixture' in sys.argv:
        c['tasks']=[{'id':'sample-task','title':'合成课程任务','path':'01_课程学习/测试课程','storage':'active','lifecycle':'active'}]
        c['explicit_preferences']=[{'id':'legacy-test','identity':'synthetic legacy preference','destination':'01_课程学习/测试课程','move_as_unit':True,'reason':'test'}]
    if '--rules-fixture' in sys.argv:
        c['explicit_preferences']=[{'id':'legacy-fixture','identity':'synthetic example bundle','destination':'01_课程学习/测试课程','reason':'existing structured preference','move_as_unit':True}]
    write_json(root/'_整理规则/分类配置.json',c)
    for name,text in [('note.txt','course'),('other.txt','course'),('unknown.txt','<script>window.injected=true</script>')]:
        (root/'00_收件箱'/name).write_text(text,encoding='utf-8')
    server=make_server(root,base/'profile')
    server.desktop.credential_vault=CredentialVault(base/'test-credentials.sqlite3')
    if '--rules-fixture' in sys.argv:
        original_perform=server.desktop.perform
        def delayed_rules(app,action,body,job):
            if action=='rules-simulate':time.sleep(1)
            return original_perform(app,action,body,job)
        server.desktop.perform=delayed_rules
        original_directory=server.desktop.directory
        def delayed_directory(*args,**kwargs):
            time.sleep(.2);return original_directory(*args,**kwargs)
        server.desktop.directory=delayed_directory
    if '--ocr-fixture' in sys.argv:
        from test_ocr import sample_image,native_pdf
        from PIL import Image
        from pypdf import PdfWriter
        # This is a generated test file, never a user's download or document.
        for fixture_file in (root/'00_收件箱').iterdir():fixture_file.unlink()
        sample_image(root/'00_收件箱/screenshot.png')
        native_pdf(root/'00_收件箱/native.pdf')
        with Image.open(root/'00_收件箱/screenshot.png') as image:image.save(base/'scan.pdf','PDF')
        writer=PdfWriter();writer.append(root/'00_收件箱/native.pdf');writer.append(base/'scan.pdf')
        with (root/'00_收件箱/mixed.pdf').open('wb') as output:writer.write(output)
    if '--overview-fixture' in sys.argv:
        from personalstorage import overview
        for category in c['categories']:(root/category['root']).mkdir(exist_ok=True)
        for relative in ('02_项目/智能小车/硬件资料','03_竞赛/智能车竞赛','06_资料库/学科资料/控制理论','06_资料库/文档与PPT模板/优秀PPT案例'):(root/relative).mkdir(parents=True,exist_ok=True)
        (root/'06_资料库/学科资料/控制理论/控制理论笔记.md').write_text('synthetic control notes',encoding='utf-8')
        original_search=overview.search
        def delayed_search(*args,**kwargs):
            time.sleep(1.2)
            return original_search(*args,**kwargs)
        overview.search=delayed_search
    if '--ai-fixture' in sys.argv or '--workspace-fixture' in sys.argv or '--intelligent-fixture' in sys.argv or '--beta-fixture' in sys.argv:
        from test_ai import FakeProvider
        provider=FakeProvider()
        server.desktop.credential_vault.save('sk-'+'synthetic-fixture-key'*2)
        server.desktop.ai_provider_factory=lambda *a,**k:provider
        with server.desktop.app() as app:AIManager(app,server.desktop.credential_vault).configure({'enabled':True})
    if '--workspace-fixture' in sys.argv:
        from personalstorage import activity,scanning
        old_scan=scanning.scan
        def delayed_scan(*args,**kwargs):
            time.sleep(1.5)
            return old_scan(*args,**kwargs)
        scanning.scan=delayed_scan
        old_test=provider.test
        def delayed_test():
            time.sleep(1.5)
            return old_test()
        provider.test=delayed_test
        with server.desktop.app() as app:
            for index in range(180):
                activity.begin(app.state,'fixture-'+str(index),'scan','inbox')
                activity.update(app.state,'fixture-'+str(index),'done',result={'rows':[]})
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    downloads=base/'downloads';downloads.mkdir();(downloads/'download.txt').write_text('course')
    print(json.dumps({'url':server.desktop.url,'root':str(root),'downloads':str(downloads),'stop':str(base/'stop')}),flush=True)
    try:
        while not (base/'stop').exists(): time.sleep(.2)
    finally:
        server.shutdown();thread.join();server.server_close();server.desktop.close()
