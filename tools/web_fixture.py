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

with tempfile.TemporaryDirectory(prefix='storage-ui-') as tmp:
    base=Path(tmp); root=base/'library'
    (root/'00_收件箱').mkdir(parents=True)
    (root/'01_课程学习/测试课程').mkdir(parents=True)
    c=config(); c['executable_rules'][0]['all']=[{'field':'text','op':'contains','value':'course'}]
    write_json(root/'_整理规则/分类配置.json',c)
    for name,text in [('note.txt','course'),('other.txt','course'),('unknown.txt','<script>window.injected=true</script>')]:
        (root/'00_收件箱'/name).write_text(text,encoding='utf-8')
    server=make_server(root,base/'profile')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    print(json.dumps({'url':server.desktop.url,'root':str(root),'stop':str(base/'stop')}),flush=True)
    try:
        while not (base/'stop').exists(): time.sleep(.2)
    finally:
        server.shutdown();thread.join();server.server_close();server.desktop.close()
