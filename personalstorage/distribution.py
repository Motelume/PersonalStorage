"""Explicit prerelease discovery, verified installer staging and app rollback."""
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from . import __version__
from .common import SafetyError,sha,uid,write_json
from .paths import runtime_home,program_home
from .recognition import download

API='https://api.github.com/repos/Motelume/PersonalStorage/releases?per_page=20'

def version(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:b(\d+)|-beta\.(\d+))?',value)
    if not match:raise SafetyError('版本格式无效')
    return tuple(map(int,match.groups()[:3]))+(int(match[4] or match[5] or 9999),)

def installed():
    base=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'Programs/PersonalStorage';rows=[]
    if base.is_dir():
        for p in base.iterdir():
            try:version(p.name)
            except SafetyError:continue
            if (p/'PersonalStorage.exe').is_file():rows.append({'version':p.name,'path':str(p/'PersonalStorage.exe'),'current':p.resolve()==program_home().resolve()})
    return sorted(rows,key=lambda r:version(r['version']),reverse=True)

def check():
    request=urllib.request.Request(API,headers={'Accept':'application/vnd.github+json','User-Agent':'PersonalStorage/'+__version__})
    with urllib.request.urlopen(request,timeout=20) as response:rows=json.load(response)
    releases=[]
    for row in rows:
        try:v=version(row['tag_name'])
        except (KeyError,SafetyError):continue
        if v[0]>=1 or row.get('draft'):continue
        assets={r['name']:r['browser_download_url'] for r in row.get('assets',[])}
        installer=next((n for n in assets if re.fullmatch(r'PersonalStorage-Setup-[0-9.ab-]+-x64\.exe',n)),None)
        if installer and 'SHA256SUMS.txt' in assets:releases.append({'version':row['tag_name'].lstrip('v'),'installer_name':installer,'url':assets[installer],'checksums_url':assets['SHA256SUMS.txt'],'page':row['html_url'],'prerelease':row.get('prerelease',False)})
    latest=max(releases,key=lambda r:version(r['version'])) if releases else None
    return {'current':__version__,'latest':latest,'available':bool(latest and version(latest['version'])>version(__version__)),'channel':'prerelease','one_point_zero_requires_user_confirmation':True}

def stage(app,release):
    for key in ('url','checksums_url','page'):
        if not release[key].startswith('https://github.com/Motelume/PersonalStorage/'):raise SafetyError('更新地址不属于本项目')
    if version(release['version'])[0]>=1:raise SafetyError('1.0及以上需先由用户确认发布政策')
    with urllib.request.urlopen(release['checksums_url'],timeout=30) as response:text=response.read(20000).decode('utf-8')
    expected=None
    for line in text.splitlines():
        parts=line.split()
        if len(parts)==2 and parts[1].lstrip('*')==release['installer_name']:expected=parts[0]
    if not expected or not re.fullmatch('[0-9a-fA-F]{64}',expected):raise SafetyError('未找到安装包校验值')
    target=runtime_home()/'updates'/release['installer_name'];target.parent.mkdir(parents=True,exist_ok=True)
    download(release['url'],target,expected,max_bytes=512*1024*1024)
    from .backup import create_management
    saved=create_management(app)
    return {'path':str(target),'sha256':sha(target),'backup':saved['path'],'version':release['version'],'publisher_signature':'待安装器显示验证；当前按测试版分发'}

def launch(staged):
    path=Path(staged['path']);base=runtime_home()/'updates'
    if not path.resolve().is_relative_to(base.resolve()) or sha(path)!=staged['sha256']:raise SafetyError('已下载安装包发生变化')
    subprocess.Popen([str(path)],creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0));return {'installer_started':True,'backup':staged['backup']}

def rollback_preview(app,value):
    row=next((r for r in installed() if r['version']==value),None)
    if not row:raise SafetyError('未找到该本机版本')
    if version(value)<version('0.10.0b1') and any('when' in r or any(c['field']=='extension' for c in r.get('all',[])) for r in app.policy.data['executable_rules']):raise SafetyError('旧版不支持当前嵌套条件或扩展名字段，请先恢复兼容规则快照')
    return {**row,'root':str(app.root),'policy_hash':app.policy.hash}

def rollback(app,preview):
    from .backup import create_management
    if app.policy.hash!=preview['policy_hash']:raise SafetyError('规则已变化，重新检查回退')
    backup=create_management(app);target=Path(preview['path'])
    if target not in [Path(r['path']) for r in installed()]:raise SafetyError('程序版本位置变化')
    app.state.set('automation',None)
    if os.name=='nt':
        command=['powershell.exe','-NoProfile','-NonInteractive','-Command',"$w=New-Object -ComObject WScript.Shell; $p=$env:PS_ROLLBACK_LINK; if(Test-Path -LiteralPath $p){$s=$w.CreateShortcut($p);$s.TargetPath=$env:PS_ROLLBACK_EXE;$s.WorkingDirectory=Split-Path -Parent $env:PS_ROLLBACK_EXE;$s.Save()}"]
        env={**os.environ,'PS_ROLLBACK_LINK':str(app.root/'PersonalStorage.lnk'),'PS_ROLLBACK_EXE':str(target)}
        subprocess.run(command,env=env,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return {'rollback_prepared':True,'version':preview['version'],'path':str(target),'backup':backup['path'],'next_step':'退出当前桌面程序，再从现有资料库入口打开所选版本。网页版需单独重启。'}
