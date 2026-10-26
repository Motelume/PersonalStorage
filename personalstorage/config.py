"""Machine-specific settings stay outside versioned source."""
import os
import sys
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from .common import read_json, SafetyError, linked
from .paths import canonical_root,runtime_home,program_home


def saved_root():
    path=runtime_home()/'machine.sqlite3'
    if not path.is_file():return None
    with closing(sqlite3.connect(path)) as db:
        version=db.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
        if version and version[0]!='1':raise SafetyError('本机路径配置版本不兼容，请显式指定资料目录')
        row=db.execute("SELECT value FROM settings WHERE key='root'").fetchone()
    return row[0] if row else None


def save_root(root):
    from .policy import Policy
    root=canonical_root(root)
    validate_location(root)
    Policy(root)
    path=runtime_home()/'machine.sqlite3';path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('schema_version','1'))
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('root',str(root)))
    return str(root)


def validate_location(root):
    root=canonical_root(root);program=program_home()
    if linked(root):raise SafetyError('资料目录不能是链接或junction')
    if root==program or root.is_relative_to(program) or program.is_relative_to(root):
        raise SafetyError('资料目录与程序目录必须分开，不能互相包含')
    if runtime_home().is_relative_to(root):raise SafetyError('资料目录不能包含本机运行状态目录')
    return root


def default_root():
    override = os.environ.get('PERSONALSTORAGE_ROOT')
    if override:
        return str(canonical_root(override))
    program = program_home()
    local = program / '.local' / 'config.json'
    if local.is_file():
        data = read_json(local)
        if not isinstance(data, dict) or not isinstance(data.get('root'), str) or not data['root'].strip():
            raise SafetyError('本机配置需包含非空root字符串')
        root = Path(data['root']).expanduser()
        if not root.is_absolute():
            raise SafetyError('本机root必须为绝对路径')
        return str(root)
    return saved_root() or r'D:\PersonalStorage'
