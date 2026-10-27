"""Cancelable owned helper processes; Windows job closes only our descendants."""
import subprocess
import time
import ctypes
import tempfile
from .tasks import checkpoint


def run(args,timeout,check=True,capture_output=False,text=False,encoding=None,creationflags=0,**options):
    out=tempfile.TemporaryFile() if capture_output else None;err=tempfile.TemporaryFile() if capture_output else None
    if capture_output:options.update(stdout=out,stderr=err)
    proc=subprocess.Popen(args,creationflags=creationflags|getattr(subprocess,'CREATE_NO_WINDOW',0),**options);handle=None
    if hasattr(ctypes,'windll'):
        from ctypes import wintypes
        class BASIC(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
        class IO(ctypes.Structure):_fields_=[(n,ctypes.c_ulonglong) for n in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class INFO(ctypes.Structure):_fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        kernel=ctypes.windll.kernel32;kernel.CreateJobObjectW.restype=wintypes.HANDLE;kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.CreateJobObjectW(None,None);info=INFO();info.BasicLimitInformation.LimitFlags=0x2000
        if not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle,wintypes.HANDLE(int(proc._handle))):
            if handle:kernel.CloseHandle(handle)
            handle=None
    started=time.monotonic()
    try:
        while proc.poll() is None:
            checkpoint()
            if time.monotonic()-started>timeout:raise subprocess.TimeoutExpired(args,timeout)
            time.sleep(.15)
        if check and proc.returncode:raise subprocess.CalledProcessError(proc.returncode,args)
        values=[]
        for stream in (out,err):
            if stream:
                stream.seek(0);value=stream.read(2*1024*1024);values.append(value.decode(encoding or 'utf-8',errors='replace') if text else value)
            else:values.append(None)
        return subprocess.CompletedProcess(args,proc.returncode,*values)
    finally:
        if handle:ctypes.windll.kernel32.CloseHandle(handle)
        elif proc.poll() is None:proc.kill()
        proc.wait(timeout=10)
        if out:out.close()
        if err:err.close()
