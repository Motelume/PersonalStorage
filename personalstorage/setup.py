"""Create an empty local library from generic templates; never import documents."""
from pathlib import Path
from .common import SafetyError, read_json, write_json, linked, safe_path


def setup_library(root):
    root = Path(root).absolute()
    templates = Path(__file__).with_name('templates')
    if root == Path(__file__).resolve().parents[1]:
        raise SafetyError('资料目录必须与程序仓库分开')
    if linked(root):
        raise SafetyError('资料目录不能是链接')
    config_path = safe_path(root, '_整理规则/分类配置.json')
    agent_path = safe_path(root, 'AGENTS.md')
    rules_path = safe_path(root, '_整理规则/整理规则.md')
    if any(p.exists() for p in (config_path, agent_path, rules_path)):
        raise SafetyError('目录已含配置或整理规则，不覆盖；已有资料库请直接validate')
    policy = read_json(templates / 'classification-config.json')
    policy['library_root'] = str(root)
    for category in policy['categories']:
        safe_path(root, category['root']).mkdir(parents=True, exist_ok=True)
    safe_path(root, policy['review_path']).mkdir(parents=True, exist_ok=True)
    for destination, source in ((agent_path, 'AGENTS.md'), (rules_path, 'classification.md')):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('x', encoding='utf-8') as stream:
            stream.write((templates / source).read_text(encoding='utf-8'))
    write_json(config_path, policy)
    return {'root': str(root), 'created': True, 'executable_rules': 0, 'original_files_touched': False}
