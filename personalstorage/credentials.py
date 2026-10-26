"""Current-user DPAPI encryption; no plaintext secret is stored or returned to UI."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sqlite3
from contextlib import closing
from .common import SafetyError
from .paths import runtime_home


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(data, decrypt=False):
    if os.name != 'nt': raise SafetyError('密钥安全存储需要Windows当前用户环境')
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer); result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), 'PersonalStorage DeepSeek', None, None, None, 1, ctypes.byref(result))
    if not ok: raise SafetyError('无法访问当前Windows用户的加密密钥存储')
    try: return ctypes.string_at(result.data, result.size)
    finally: kernel.LocalFree(result.data)


class CredentialVault:
    def __init__(self, path=None):
        self.path = Path(path) if path else runtime_home()/'credentials.sqlite3'

    def configured(self):
        if not self.path.is_file(): return False
        with closing(sqlite3.connect(self.path)) as db:
            if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='secrets'").fetchone(): return False
            return bool(db.execute("SELECT COUNT(*) FROM secrets WHERE name='deepseek'").fetchone()[0])

    def save(self, key):
        key = key.strip()
        if not key.startswith('sk-') or not 20 <= len(key) <= 256 or any(c.isspace() for c in key):
            raise SafetyError('请输入有效的DeepSeek API密钥')
        encrypted = protect(key.encode('utf-8'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS secrets(name TEXT PRIMARY KEY,value BLOB NOT NULL)')
                db.execute('INSERT OR REPLACE INTO secrets VALUES(?,?)', ('deepseek', encrypted))

    def read(self):
        if not self.configured(): raise SafetyError('尚未保存DeepSeek API密钥')
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT value FROM secrets WHERE name='deepseek'").fetchone()
        return protect(row[0], decrypt=True).decode('utf-8')

    def clear(self):
        if self.path.is_file():
            with closing(sqlite3.connect(self.path)) as db:
                with db: db.execute("DELETE FROM secrets WHERE name='deepseek'")
