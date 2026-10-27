"""One native window per library; web and desktop remain independent surfaces."""
import ctypes
import json
import os
from pathlib import Path
from .common import digest
from .paths import canonical_root,runtime_home

_handles=[]
def claim(root):
    if os.name!='nt':return True
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateMutexW.restype=wintypes.HANDLE;kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
    name='Local\\PersonalStorage-'+digest(str(canonical_root(root)).casefold())[:24]
    handle=kernel.CreateMutexW(None,False,name);exists=ctypes.get_last_error()==183
    if not handle:raise OSError('无法建立桌面实例锁')
    _handles.append(handle)
    if not exists:return True
    user=ctypes.windll.user32
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    @callback
    def visit(hwnd,param):
        length=user.GetWindowTextLengthW(hwnd)
        if length:
            title=ctypes.create_unicode_buffer(length+1);user.GetWindowTextW(hwnd,title,length+1)
            if title.value=='PersonalStorage':user.ShowWindow(hwnd,9);user.SetForegroundWindow(hwnd);return False
        return True
    user.EnumWindows(visit,0);return False
