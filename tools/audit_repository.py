"""Audit the Git index before publishing; never read the user's data library."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {'.gitignore', '.gitattributes', 'README.md', 'SECURITY.md',
              'pyproject.toml', 'requirements-optional.txt', 'requirements-desktop.txt', 'run.cmd', '启动界面.cmd','启动桌面版.cmd'}
SENSITIVE = [re.compile(r'[A-Za-z]:[/\\]Users[/\\][^/\\\s]+', re.I),
             re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}\b'),
             re.compile(r'\bgithub_pat_[A-Za-z0-9_]{30,}\b'),
             re.compile(r'\bsk-[A-Za-z0-9_-]{25,}\b'),
             re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]


def allowed(name):
    p = PurePosixPath(name)
    if name in ROOT_FILES: return True
    if len(p.parts) == 2 and p.parts[0] in ('personalstorage', 'tests') and p.suffix == '.py': return True
    if name in ('personalstorage/web_static/index.html', 'personalstorage/web_static/app.js',
                'personalstorage/web_static/app.css','personalstorage/web_static/ai.js', 'personalstorage/templates/AGENTS.md',
                'personalstorage/templates/classification.md','personalstorage/templates/local-rules.md', 'personalstorage/templates/classification-config.json',
                'schemas/review-result.schema.json', 'docs/architecture.md','docs/ai-guide.md','docs/desktop-guide.md','docs/portability.md','docs/workspace-guide.md',
                '.github/workflows/tests.yml', 'tools/audit_repository.py',
                'tools/frontend_smoke.cjs','tools/frontend_ai_smoke.cjs','tools/frontend_workspace_smoke.cjs', 'tools/web_fixture.py','tools/desktop_entry.py','tools/build_desktop.py'): return True
    return False


def main():
    def git(*args):
        return subprocess.check_output(['git', '-C', str(ROOT), *args])
    names = [n.decode('utf-8') for n in git('ls-files', '--cached', '-z').split(b'\0') if n]
    if not names: raise ValueError('暂存区为空：先选择要提交的代码')
    failures=[]; files=[]
    for name in names:
        if not allowed(name): failures.append(name + ': 不在源代码允许清单'); continue
        data=git('show', ':'+name)
        if len(data)>1024*1024 or b'\0' in data: failures.append(name+': 二进制或超限'); continue
        try: text=data.decode('utf-8-sig')
        except UnicodeDecodeError: failures.append(name+': 不是UTF-8文本'); continue
        if any(pattern.search(text) for pattern in SENSITIVE): failures.append(name+': 可能含私有路径或凭据'); continue
        files.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    if failures: raise ValueError('\n'.join(failures))
    print(json.dumps({'ok':True,'files':len(files),'total_bytes':sum(f['bytes'] for f in files),
                      'manifest_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
                      'scope':'staged source code and generic templates only'},ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:
        print(str(exc),file=sys.stderr);raise SystemExit(2)
