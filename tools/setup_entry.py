"""Self-contained user installer/uninstaller; never packages or deletes user data."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
import zipfile
from contextlib import closing
from pathlib import Path

MAGIC='PersonalStorage-install-v1'

def file_hash(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def local_home():return Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))
def default_base():return local_home()/'Programs/PersonalStorage'

def data_root():
    machine=local_home()/'PersonalStorage/machine.sqlite3'
    if machine.is_file():
        with closing(sqlite3.connect(machine.as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute("SELECT value FROM settings WHERE key='root'").fetchone()
        if row:return Path(row[0])
    return Path('D:/PersonalStorage')

def validate_base(value):
    base=Path(value).absolute();resolved=base.resolve()
    if not base.is_absolute() or len(resolved.parts)<3:raise ValueError('安装位置不能是磁盘根目录')
    for p in (base,*base.parents):
        if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise ValueError('安装位置不能经过链接')
    for private in (data_root().resolve(),(local_home()/'PersonalStorage').resolve()):
        if resolved==private or resolved.is_relative_to(private) or private.is_relative_to(resolved):raise ValueError('安装位置必须与资料及管理状态分开')
    return base

def safe(base,relative):
    p=Path(relative)
    if p.is_absolute() or any(x in ('.','..','') for x in p.parts) or ':' in relative:raise ValueError('安装清单路径无效')
    target=base/p
    if not target.resolve().is_relative_to(base.resolve()):raise ValueError('安装清单越界')
    for part in (target,*target.parents):
        if part==base:break
        if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):raise ValueError('拒绝安装目录中的链接')
    return target

def atomic_json(path,value):
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    try:temp.replace(path)
    except OSError as exc:
        if getattr(exc,'winerror',None)!=17:raise
        with temp.open('rb') as source,path.open('wb' if path.exists() else 'xb') as target:shutil.copyfileobj(source,target);target.flush();os.fsync(target.fileno())
        temp.unlink()

def backup_state():
    root=data_root();state_home=local_home()/'PersonalStorage'
    identity=hashlib.sha256(json.dumps(str(root).casefold(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    folder=state_home/identity;dbfile=folder/'state.sqlite3'
    if not dbfile.is_file():return None
    destination=folder/'upgrade-backups'/('installer-'+uuid.uuid4().hex);destination.mkdir(parents=True)
    source=sqlite3.connect(dbfile.as_uri()+'?mode=ro',uri=True);target=sqlite3.connect(destination/'state.sqlite3')
    try:source.backup(target)
    finally:source.close();target.close()
    config=root/'_整理规则/分类配置.json'
    if config.is_file():shutil.copy2(config,destination/'classification-config.json')
    return str(destination)

def shortcuts(base,exe,desktop=False):
    start=Path(os.environ.get('APPDATA',Path.home()/'AppData/Roaming'))/'Microsoft/Windows/Start Menu/Programs/PersonalStorage.lnk'
    paths=[start];root=data_root();existing=root/'PersonalStorage.lnk'
    if existing.is_file():paths.append(existing)
    if desktop:paths.append(Path.home()/'Desktop/PersonalStorage.lnk')
    script="$w=New-Object -ComObject WScript.Shell; foreach($p in ($env:PS_LINKS | ConvertFrom-Json)){[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($p)) | Out-Null;$s=$w.CreateShortcut($p);$s.TargetPath=$env:PS_EXE;$s.WorkingDirectory=[IO.Path]::GetDirectoryName($env:PS_EXE);$s.IconLocation=$env:PS_EXE+',0';$s.Save()}"
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env={**os.environ,'PS_LINKS':json.dumps([str(p) for p in paths]),'PS_EXE':str(exe)},check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return [str(p) for p in paths]

def registry(base,version):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Uninstall\PersonalStorage') as key:
        values={'DisplayName':'PersonalStorage（测试版）','DisplayVersion':version,'Publisher':'Motelume','InstallLocation':str(base),'UninstallString':'"'+str(base/'maintenance/PersonalStorage-Uninstall.exe')+'" --uninstall --base "'+str(base)+'"','DisplayIcon':str(base/version/'PersonalStorage.exe')}
        for name,value in values.items():winreg.SetValueEx(key,name,0,winreg.REG_SZ,value)

def install(base,payload,manifest,register=True,desktop=False):
    base=validate_base(base);meta=json.loads(Path(manifest).read_text(encoding='utf-8'));version=meta['version']
    if not version.startswith('0.') or not meta.get('prerelease'):raise ValueError('未经用户确认，安装器仅分发0.x测试版')
    if file_hash(payload)!=meta['payload_sha256']:raise ValueError('安装载荷SHA-256不一致')
    base.mkdir(parents=True,exist_ok=True);target=base/version
    if target.exists():
        marker=target/'.installed-files.json'
        if marker.is_file():
            ready=json.loads(marker.read_text(encoding='utf-8'))
            if ready.get('payload_sha256')==meta['payload_sha256'] and all(safe(target,r['path']).is_file() and file_hash(safe(target,r['path']))==r['sha256'] for r in ready['files']):
                return {**finish_install(base,target,version,backup_state() if register else None,register,desktop),'already_installed':True}
        raise ValueError('该版本位置已存在，保留原位置；请先核对或选择其他安装目录')
    stage=base/('.partial-'+uuid.uuid4().hex);stage.mkdir();files=[]
    with zipfile.ZipFile(payload) as archive:
        if sum(i.file_size for i in archive.infolist())>1024*1024*1024:raise ValueError('安装载荷超出预算')
        if shutil.disk_usage(base).free<sum(i.file_size for i in archive.infolist())+100*1024*1024:raise ValueError('安装目录空间不足')
        for info in archive.infolist():
            if info.is_dir():continue
            name=info.filename
            if not name.startswith('PersonalStorage/'):raise ValueError('载荷布局错误')
            relative=name[len('PersonalStorage/'):];out=safe(stage,relative);out.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(info) as source,out.open('xb') as dest:shutil.copyfileobj(source,dest)
            files.append({'path':relative,'sha256':file_hash(out)})
    if not (stage/'PersonalStorage.exe').is_file():raise ValueError('载荷缺少桌面入口')
    backup=backup_state() if register else None
    atomic_json(stage/'.installed-files.json',{'magic':MAGIC,'version':version,'files':files,'payload_sha256':meta['payload_sha256']})
    # Both resolved paths are checked before moving the generated program staging directory.
    if not stage.resolve().is_relative_to(base.resolve()) or not target.resolve().is_relative_to(base.resolve()):raise ValueError('安装暂存越界')
    try:stage.rename(target)
    except OSError as exc:
        if getattr(exc,'winerror',None)!=17:raise
        # Redirected user folders may have distinct backing volumes. Copy only generated
        # program files into a new version directory, then verify before publishing any entry.
        shutil.copytree(stage,target,dirs_exist_ok=False)
        if any(file_hash(safe(target,row['path']))!=row['sha256'] for row in files):raise ValueError('安装副本校验失败，旧版本与入口保持原状')
    return finish_install(base,target,version,backup,register,desktop)

def install_state(base):
    dbfile=base/'personalstorage-install.sqlite3'
    if dbfile.is_file():
        with closing(sqlite3.connect(dbfile)) as db:
            row=db.execute('SELECT value FROM installation WHERE id=1').fetchone()
        if row:return json.loads(row[0])
    statefile=base/'installation.json'
    return json.loads(statefile.read_text(encoding='utf-8')) if statefile.is_file() else {'magic':MAGIC,'versions':[],'shortcuts':[]}

def save_install_state(base,state):
    with closing(sqlite3.connect(base/'personalstorage-install.sqlite3')) as db:
        db.execute('CREATE TABLE IF NOT EXISTS installation(id INTEGER PRIMARY KEY,value TEXT)');db.execute('INSERT OR REPLACE INTO installation VALUES(1,?)',(json.dumps(state,ensure_ascii=False),));db.commit()
    atomic_json(base/'installation.json',state)

def finish_install(base,target,version,backup,register,desktop):
    state=install_state(base)
    if state.get('magic')!=MAGIC:raise ValueError('安装元数据已被其他内容占用')
    state['active']=version;state['versions']=list(dict.fromkeys(state['versions']+[version]));state['last_backup']=backup
    if register:
        state['shortcuts']=list(dict.fromkeys(state['shortcuts']+shortcuts(base,target/'PersonalStorage.exe',desktop)))
        maintenance=base/'maintenance';maintenance.mkdir(exist_ok=True)
        bundled=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'PersonalStorage-Uninstall.exe'
        if bundled.is_file():shutil.copy2(bundled,maintenance/'PersonalStorage-Uninstall.exe')
        registry(base,version)
    save_install_state(base,state);return {'installed':True,'version':version,'path':str(target),'backup':backup,'data_preserved':True}

def uninstall(base,register=True):
    base=validate_base(base);statefile=base/'installation.json';state=install_state(base)
    if state.get('magic')!=MAGIC:raise ValueError('缺少有效安装清单，拒绝删除')
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateFileW.restype=wintypes.HANDLE;kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        for version in state['versions']:
            exe=safe(base,version)/'PersonalStorage.exe'
            if not exe.exists():continue
            handle=kernel.CreateFileW(str(exe),0x10000,0,None,3,0,None)
            if handle==wintypes.HANDLE(-1).value:raise ValueError('请先关闭PersonalStorage窗口和服务，再卸载；原资料保持原位')
            kernel.CloseHandle(handle)
    preserved=[];removed=[]
    for version in state['versions']:
        folder=safe(base,version);marker=folder/'.installed-files.json'
        if not marker.is_file():continue
        data=json.loads(marker.read_text(encoding='utf-8'))
        if data.get('magic')!=MAGIC:continue
        for item in data['files']:
            p=safe(folder,item['path'])
            if p.is_file():
                if file_hash(p)!=item['sha256']:preserved.append(str(p));continue
                p.unlink();removed.append(item['path'])
        marker.unlink()
        # Remove only empty directories in the verified program subtree; no recursive deletion.
        directories=sorted([p for p in folder.rglob('*') if p.is_dir() and not p.is_symlink()],key=lambda p:len(p.parts),reverse=True)
        for directory in directories:
            if directory.resolve().is_relative_to(folder.resolve()) and not any(directory.iterdir()):directory.rmdir()
        if not any(folder.iterdir()):folder.rmdir()
    if register:
        script="$w=New-Object -ComObject WScript.Shell;foreach($p in ($env:PS_LINKS|ConvertFrom-Json)){if(Test-Path -LiteralPath $p){$s=$w.CreateShortcut($p);if($s.TargetPath.StartsWith($env:PS_BASE,[StringComparison]::OrdinalIgnoreCase)){Remove-Item -LiteralPath $p}}}"
        subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env={**os.environ,'PS_LINKS':json.dumps(state.get('shortcuts',[])),'PS_BASE':str(base)+'\\'},check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        import winreg
        try:winreg.DeleteKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Uninstall\PersonalStorage')
        except FileNotFoundError:pass
    if statefile.exists():statefile.unlink()
    dbfile=base/'personalstorage-install.sqlite3'
    if dbfile.exists():dbfile.unlink()
    return {'uninstalled':True,'removed_files':len(removed),'modified_program_files_preserved':preserved,'user_data_preserved':True,'notice':'资料、规则、索引、密钥和备份均保留；旧便携版本及卸载工具可自行保留。'}

def webview_ready():
    import winreg
    key=r'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for flag in (winreg.KEY_WOW64_32KEY,winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive,key,0,winreg.KEY_READ|flag) as reg:
                    if winreg.QueryValueEx(reg,'pv')[0] not in ('','0.0.0.0'):return True
            except OSError:pass
    return False

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base',default=str(default_base()));parser.add_argument('--silent',action='store_true');parser.add_argument('--uninstall',action='store_true');parser.add_argument('--no-register',action='store_true');parser.add_argument('--report');args=parser.parse_args()
    resource=Path(getattr(sys,'_MEIPASS',Path(__file__).parent));payload=resource/'payload.zip';manifest=resource/'payload-manifest.json'
    if args.silent:
        result=uninstall(args.base,not args.no_register) if args.uninstall else install(args.base,payload,manifest,not args.no_register)
        if args.report:Path(args.report).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        return result
    import tkinter as tk
    from tkinter import messagebox,filedialog
    import threading,webbrowser
    root=tk.Tk();root.title('PersonalStorage · '+('卸载' if args.uninstall else '安装测试版'));root.geometry('650x360');root.resizable(False,False)
    tk.Label(root,text='PersonalStorage',font=('Segoe UI',22,'bold'),fg='#3569dc').pack(anchor='w',padx=28,pady=(25,8))
    tk.Label(root,text='仅安装程序；你的资料、分类规则、索引与密钥独立保存。',font=('Microsoft YaHei UI',10)).pack(anchor='w',padx=28)
    value=tk.StringVar(value=args.base);entry=tk.Entry(root,textvariable=value,width=76);entry.pack(padx=28,pady=15)
    desktop=tk.BooleanVar(value=False)
    if not args.uninstall:tk.Checkbutton(root,text='创建桌面快捷方式（可选）',variable=desktop).pack(anchor='w',padx=24)
    status=tk.StringVar(value='WebView2 已就绪' if webview_ready() else '未检测到WebView2；首次启动前需安装官方运行时')
    tk.Label(root,textvariable=status,wraplength=590,justify='left').pack(anchor='w',padx=28,pady=8)
    if not webview_ready():tk.Button(root,text='打开微软官方 WebView2 下载页',command=lambda:webbrowser.open('https://developer.microsoft.com/microsoft-edge/webview2/')).pack(anchor='w',padx=28)
    def start():
        button.config(state='disabled');entry.config(state='disabled');status.set('正在处理，请等待校验完成…')
        def work():
            try:
                result=uninstall(value.get()) if args.uninstall else install(value.get(),payload,manifest,desktop=desktop.get())
                root.after(0,lambda:finish(result))
            except Exception as exc:root.after(0,lambda message=str(exc):failure(message))
        threading.Thread(target=work,daemon=True).start()
    def finish(result):
        status.set('处理完成，资料与本机管理状态已保留。');messagebox.showinfo('PersonalStorage',json.dumps(result,ensure_ascii=False,indent=2));root.destroy()
    def failure(message):status.set('未完成：'+message);button.config(state='normal');entry.config(state='normal')
    button=tk.Button(root,text='卸载程序，保留资料' if args.uninstall else '安装 / 升级测试版',command=start,bg='#3569dc',fg='white',padx=18,pady=8);button.pack(anchor='e',padx=28,pady=15)
    root.mainloop()

if __name__=='__main__':
    try:main()
    except Exception as exc:
        if '--report' in sys.argv:
            path=Path(sys.argv[sys.argv.index('--report')+1]);path.write_text(json.dumps({'error':str(exc)}),encoding='utf-8')
        if '--silent' in sys.argv:raise SystemExit(2)
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,str(exc),'PersonalStorage setup',0x10)
        raise SystemExit(2)
