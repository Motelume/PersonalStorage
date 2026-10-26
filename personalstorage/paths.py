"""Shared, machine-local path contract. Never follows links to hide a root alias."""
import os
from pathlib import Path
import sys


def canonical_root(value):
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def runtime_home():
    override=os.environ.get('PERSONALSTORAGE_HOME')
    return canonical_root(override) if override else Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'PersonalStorage'


def program_home():
    return Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
