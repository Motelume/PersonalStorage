"""Time-bounded standard-library regex matching in an isolated worker."""
import functools
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from .common import SafetyError


def worker(source, target):
    data=json.loads(Path(source).read_text(encoding='utf-8'))
    result=bool(re.search(data['pattern'],data['text'],re.I))
    Path(target).write_text(json.dumps(result),encoding='utf-8')


@functools.lru_cache(maxsize=256)
def search(pattern, text):
    if len(pattern)>256 or len(text)>16000:raise SafetyError('正则输入超过匹配预算')
    with tempfile.TemporaryDirectory(prefix='personalstorage-regex-') as folder:
        source=Path(folder)/'input.json';target=Path(folder)/'result.json'
        source.write_text(json.dumps({'pattern':pattern,'text':text}),encoding='utf-8')
        command=([sys.executable,'--regex-worker'] if getattr(sys,'frozen',False)
                 else [sys.executable,'-m','personalstorage.rule_regex'])+[str(source),str(target)]
        try:
            subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2,check=True,
                           cwd=str(Path(__file__).resolve().parents[1]),
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            return json.loads(target.read_text(encoding='utf-8'))
        except subprocess.TimeoutExpired as exc:raise SafetyError('正则匹配超时，保留待确认') from exc
        except (OSError,ValueError,subprocess.CalledProcessError) as exc:raise SafetyError('正则工作进程不可用，保留待确认') from exc


if __name__=='__main__':worker(*sys.argv[1:])
