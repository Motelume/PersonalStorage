"""Machine-specific settings stay outside versioned source."""
import os
import sys
from pathlib import Path
from .common import read_json, SafetyError


def default_root():
    override = os.environ.get('PERSONALSTORAGE_ROOT')
    if override:
        return str(Path(override).expanduser().absolute())
    program = Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    local = program / '.local' / 'config.json'
    if local.is_file():
        data = read_json(local)
        if not isinstance(data, dict) or not isinstance(data.get('root'), str) or not data['root'].strip():
            raise SafetyError('本机配置需包含非空root字符串')
        root = Path(data['root']).expanduser()
        if not root.is_absolute():
            raise SafetyError('本机root必须为绝对路径')
        return str(root)
    return r'D:\PersonalStorage'
