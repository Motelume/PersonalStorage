"""Delete only version-bound source members while the verified target is locked."""
from contextlib import contextmanager,ExitStack
from pathlib import Path
import ctypes
from ctypes import wintypes
import hashlib
import os
import stat
from .common import SafetyError,manifest,safe_path
from . import inboxes

def identity(path):
    value=Path(path).stat(follow_symlinks=False)
    return [value.st_dev,value.st_ino]

@contextmanager
def native_lease(path,delete=False,directory=False):
    if os.name!='nt':raise SafetyError('源文件清理只支持Windows')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    access=(0x80 if directory else 0x80000000)|(0x10000 if delete else 0)
    handle=kernel.CreateFileW(str(path),access,0 if delete else 3 if directory else 1,None,3,0x00200000|(0x02000000 if directory else 0),None)
    if handle==ctypes.c_void_p(-1).value:raise SafetyError('文件被占用或无法取得校验/清理权限，源资料保留')
    try:
        class AttributeTag(ctypes.Structure):_fields_=[('attributes',wintypes.DWORD),('tag',wintypes.DWORD)]
        info=AttributeTag();kernel.GetFileInformationByHandleEx.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        kernel.GetFileInformationByHandleEx.restype=wintypes.BOOL
        if not kernel.GetFileInformationByHandleEx(handle,9,ctypes.byref(info),ctypes.sizeof(info)) or info.attributes&1024:raise SafetyError('拒绝链接或无法核对的文件句柄')
        if bool(info.attributes&16)!=directory:raise SafetyError('文件类型已变化')
        yield kernel,handle
    finally:kernel.CloseHandle(handle)

@contextmanager
def leased_stream(kernel,handle):
    import msvcrt
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    process=kernel.GetCurrentProcess();duplicate=wintypes.HANDLE()
    kernel.DuplicateHandle.argtypes=[wintypes.HANDLE,wintypes.HANDLE,wintypes.HANDLE,ctypes.POINTER(wintypes.HANDLE),wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.DuplicateHandle.restype=wintypes.BOOL
    if not kernel.DuplicateHandle(process,handle,process,ctypes.byref(duplicate),0,False,2):raise SafetyError('无法建立校验文件流')
    try:fd=msvcrt.open_osfhandle(duplicate.value,os.O_RDONLY|os.O_BINARY|os.O_NOINHERIT)
    except BaseException:kernel.CloseHandle(duplicate);raise
    try:
        stream=os.fdopen(fd,'rb')
    except BaseException:
        os.close(fd);raise
    with stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or getattr(info,'st_file_attributes',0)&1024:raise SafetyError('不能清理链接或特殊文件')
        yield stream

@contextmanager
def locked_file(path,delete=False):
    with native_lease(path,delete) as (kernel,handle),leased_stream(kernel,handle) as stream:
        yield stream,kernel,handle

def verify_stream(stream,expected):
    info=os.fstat(stream.fileno())
    if info.st_size!=expected['size'] or info.st_mtime_ns!=expected['mtime_ns']:raise SafetyError('文件大小或修改时间已变化，停止清理原件')
    stream.seek(0);h=hashlib.sha256()
    while block:=stream.read(4*1024*1024):h.update(block)
    if h.hexdigest()!=expected['sha256']:raise SafetyError('内容校验不一致，停止清理原件')

def delete_verified_file(source,target,expected,*,check=None,target_lease=None):
    # Target sharing denies writes/rename/deletion until source deletion closes.
    with ExitStack() as stack:
        if target_lease is None:
            dst,_,__=stack.enter_context(locked_file(target));verify_stream(dst,expected)
        before=identity(source)
        with locked_file(source,delete=True) as (src,kernel,handle):
            opened=os.fstat(src.fileno())
            if before!=[opened.st_dev,opened.st_ino]:raise SafetyError('源文件身份变化，停止清理')
            verify_stream(src,expected)
            if check:check()
            class Disposition(ctypes.Structure):_fields_=[('DeleteFile',ctypes.c_ubyte)]
            value=Disposition(True)
            kernel.SetFileInformationByHandle.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
            kernel.SetFileInformationByHandle.restype=wintypes.BOOL
            if not kernel.SetFileInformationByHandle(handle,4,ctypes.byref(value),ctypes.sizeof(value)):
                raise SafetyError('无法清理已校验原件；副本保留，请关闭占用后重试')

def expected_remainder(source,original):
    """A retry may remove only unchanged original members, never later arrivals."""
    current=manifest(source);by_name={f['path']:f for f in original['files']}
    if current['kind']!=original['kind'] or not set(current.get('directories',[])).issubset(original.get('directories',[])):
        raise SafetyError('源目录出现本次归档以外的新目录，不能自动清理')
    if any(f!=by_name.get(f['path']) for f in current['files']):raise SafetyError('源资料包含新增或已变化的内容，不能自动清理')
    return current

def cleanup(app,op,expected=None,*,crash_hook=None):
    source_id=op['source'].split('/')[1]
    def check_scope():
        record=inboxes.source_record(app.state,source_id)
        if inboxes.mode(app.state,source_id)!='move' or record.get('mode_revision')!=op.get('mode_revision'):raise SafetyError('收件箱归档方式已改变，停止清理原件')
        actual=inboxes.resolve(app.root,app.state,op['source'])
        if identity(actual)!=op['source_identity']:raise SafetyError('源资料身份已改变，停止清理')
        return actual
    source=check_scope();target=safe_path(app.root,op['target']);expected=expected or op['manifest']
    if manifest(source)!=expected or manifest(target)!=op['manifest']:raise SafetyError('源或目标内容已变化，停止清理')
    source_root=inboxes.source_root(app.root,app.state,source_id)
    with ExitStack() as stack:
        targets={}
        if op['manifest']['kind']=='directory':
            stack.enter_context(native_lease(target,directory=True))
            for rel in op['manifest'].get('directories',[]):stack.enter_context(native_lease(safe_path(target,rel),directory=True))
        # Hold every target lease until cleanup finishes, without consuming a
        # Python/CRT descriptor for each member of a large package.
        for item in op['manifest']['files']:
            dst=target if op['manifest']['kind']=='file' else safe_path(target,item['path'])
            lease=stack.enter_context(native_lease(dst));targets[item['path']]=lease
            with leased_stream(*lease) as stream:verify_stream(stream,item)
        with ExitStack() as source_locks:
            # Prevent an ancestor/junction swap while source handles are opened.
            ancestors=[source_root,*list(source_root.parents)[:-1]]
            if expected['kind']=='directory':ancestors.extend([source,*[safe_path(source,rel) for rel in expected.get('directories',[])]])
            for directory in dict.fromkeys(ancestors):source_locks.enter_context(native_lease(directory,directory=True))
            check_scope()
            if manifest(source)!=expected:raise SafetyError('源资料已变化，停止清理')
            for item in expected['files']:
                check_scope()
                if expected['kind']=='file':src=source;dst=target
                else:src=safe_path(source,item['path']);dst=safe_path(target,item['path'])
                if not src.resolve().is_relative_to(source_root.resolve()):raise SafetyError('源路径已越界，停止清理')
                delete_verified_file(src,dst,item,check=check_scope,target_lease=targets[item['path']])
                with app.state.db:app.state.event(op['id'],'source_member_removed',{'path':item['path']})
                if crash_hook:crash_hook('after_source_member_removed',op)
        if expected['kind']=='directory':
            for rel in sorted(expected.get('directories',[]),key=lambda s:(-len(Path(s).parts),s)):
                check_scope();safe_path(source,rel).rmdir()
            check_scope();source.rmdir()
    # A newly arrived replacement at the old path must be left untouched.
    if source.exists():raise SafetyError('源位置出现新内容，已保留；本次未完全清理')
    return {'source_retained':False,'cleanup_complete':True}
