"""Isolated browser test data. Never points at the user's library."""
import sys
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
    (root/'00_收件箱').mkdir(parents=True)
    (root/'01_课程学习/测试课程').mkdir(parents=True)
    c=config(); c['executable_rules'][0]['all']=[{'field':'text','op':'contains','value':'course'}]
    write_json(root/'_整理规则/分类配置.json',c)
    for name,text in [('note.txt','course'),('other.txt','course'),('unknown.txt','<script>window.injected=true</script>')]:
        (root/'00_收件箱'/name).write_text(text,encoding='utf-8')
    server=make_server(root,base/'profile')
    server.desktop.credential_vault=CredentialVault(base/'test-credentials.sqlite3')
    if '--ai-fixture' in sys.argv or '--workspace-fixture' in sys.argv or '--intelligent-fixture' in sys.argv:
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
