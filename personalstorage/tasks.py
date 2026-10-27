"""Cooperative cancellation only at safe boundaries; no implicit write replay."""
from contextvars import ContextVar
from contextlib import contextmanager
from .common import SafetyError

class Cancelled(SafetyError):pass
_stop=ContextVar('storage_task_stop',default=None)
def current_stop():return _stop.get()

def alive(pid):
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.windll.kernel32;kernel.OpenProcess.restype=wintypes.HANDLE;kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return False
    try:
        code=wintypes.DWORD();return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code)) and code.value==259)
    finally:kernel.CloseHandle(handle)

def checkpoint():
    event=_stop.get()
    if event is not None and event.is_set():raise Cancelled('任务已停止，已完成的结果保留；文件操作不自动重放')

@contextmanager
def scope(event):
    token=_stop.set(event)
    try:yield
    finally:_stop.reset(token)
