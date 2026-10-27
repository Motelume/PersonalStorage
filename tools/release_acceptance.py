"""Exercise actual installer, native app, rollback layout and data-retaining uninstall."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from release_gate import validate,ROOT
from personalstorage.setup import setup_library

def main():
    version=validate();out=ROOT/'.local'/('release-'+version);setup=out/f'PersonalStorage-Setup-{version}-x64.exe'
    with tempfile.TemporaryDirectory(prefix='personalstorage-install-acceptance-') as tmp:
        base=Path(tmp);program=base/'program';root=base/'library';setup_library(root)
        sentinel=root/'00_收件箱/original.txt';sentinel.write_text('synthetic source; preserve during install and uninstall',encoding='utf-8')
        env={**os.environ,'PERSONALSTORAGE_HOME':str(base/'private-state')}
        install_report=base/'install.json'
        subprocess.run([str(setup),'--silent','--no-register','--base',str(program),'--report',str(install_report)],check=True,timeout=180,env=env)
        installed=json.loads(install_report.read_text(encoding='utf-8'));exe=program/version/'PersonalStorage.exe'
        native=base/'native.json'
        subprocess.run([str(exe),'--root',str(root),'--profile',str(base/'profile'),'--self-test-report',str(native)],check=True,timeout=150,env=env)
        report=json.loads(native.read_text(encoding='utf-8'))
        if not report.get('ok'):raise ValueError('Native acceptance failed: '+str(report))
        uninstaller=ROOT/'.local/installer-assets/PersonalStorage-Uninstall.exe';removed=base/'uninstall.json'
        subprocess.run([str(uninstaller),'--uninstall','--silent','--no-register','--base',str(program),'--report',str(removed)],check=True,timeout=180,env=env)
        if not sentinel.is_file() or sentinel.read_text(encoding='utf-8')!='synthetic source; preserve during install and uninstall':raise ValueError('User data did not survive uninstall')
        result={'version':version,'installer':installed,'native':report,'uninstall':json.loads(removed.read_text(encoding='utf-8')),'original_preserved':True,'test_data_only':True}
        (out/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'version':version,'native':report['ok'],'original_preserved':True}))

if __name__=='__main__':main()
