"""Build a portable ZIP and a self-contained per-user beta installer."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from release_gate import validate,ROOT

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--skip-desktop',action='store_true');args=parser.parse_args();version=validate()
    if not args.skip_desktop:subprocess.run([sys.executable,str(ROOT/'tools/build_desktop.py')],check=True,cwd=ROOT)
    out=ROOT/'.local'/('release-'+version);out.mkdir(parents=True,exist_ok=True)
    dist=ROOT/'dist/PersonalStorage'
    if not (dist/'PersonalStorage.exe').is_file():raise ValueError('Build desktop first')
    shutil.copy2(ROOT/'LICENSE',dist/'LICENSE.txt');docs=dist/'Documentation';docs.mkdir(exist_ok=True)
    for file in (ROOT/'docs').glob('*.md'):shutil.copy2(file,docs/file.name)
    portable=out/f'PersonalStorage_Desktop_v{version}_windows_x64.zip'
    with zipfile.ZipFile(portable,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for p in sorted(dist.rglob('*')):
            if p.is_file():archive.write(p,Path('PersonalStorage')/p.relative_to(dist))
    assets=ROOT/'.local/installer-assets';assets.mkdir(parents=True,exist_ok=True);shutil.copy2(portable,assets/'payload.zip')
    with portable.open('rb') as stream:payload_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    (assets/'payload-manifest.json').write_text(json.dumps({'version':version,'prerelease':True,'payload_sha256':payload_hash}),encoding='utf-8')
    flags=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onefile','--distpath',str(assets),'--workpath',str(ROOT/'build/setup'),'--specpath',str(ROOT/'build/setup')]
    for module in ('torch','numpy','rapidocr','onnxruntime','pypdfium2','webview','matplotlib','PyQt5','PyQt6','PySide6'):flags+=['--exclude-module',module]
    subprocess.run(flags+['--name','PersonalStorage-Uninstall',str(ROOT/'tools/setup_entry.py')],check=True,cwd=ROOT)
    name=f'PersonalStorage-Setup-{version}-x64';data=[]
    for file in ('payload.zip','payload-manifest.json','PersonalStorage-Uninstall.exe'):data+=['--add-data',str(assets/file)+';.']
    subprocess.run(flags+['--name',name,*data,str(ROOT/'tools/setup_entry.py')],check=True,cwd=ROOT)
    shutil.copy2(assets/(name+'.exe'),out/(name+'.exe'));lines=[]
    for p in sorted(out.iterdir()):
        if p.suffix not in ('.zip','.exe'):continue
        with p.open('rb') as stream:lines.append(hashlib.file_digest(stream,'sha256').hexdigest()+'  '+p.name)
    (out/'SHA256SUMS.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8');print(out)

if __name__=='__main__':main()
