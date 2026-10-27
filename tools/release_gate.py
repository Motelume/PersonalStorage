"""A major release cannot be built accidentally before human sign-off."""
import json
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def validate():
    source=(ROOT/'personalstorage/__init__.py').read_text(encoding='utf-8')
    version=re.search(r"__version__ = '([^']+)'",source)[1]
    project=(ROOT/'pyproject.toml').read_text(encoding='utf-8')
    if f'version = "{version}"' not in project:raise ValueError('Program and package versions differ')
    policy=json.loads((ROOT/'release-policy.json').read_text(encoding='utf-8'))
    if int(version.split('.')[0])>=1 and not policy.get('user_approved_1_0'):raise ValueError('1.0 requires explicit user approval; keep a 0.x prerelease')
    if not policy.get('user_approved_1_0') and not re.fullmatch(r'0\.\d+\.\d+b\d+',version):raise ValueError('Use an explicit 0.x beta version until sign-off')
    return version

if __name__=='__main__':print(validate())
