"""Create an empty local library from generic templates; never import documents."""
from pathlib import Path
from .common import SafetyError, read_json, write_json, linked, safe_path, relpath
from .config import validate_location
from .paths import canonical_root


def setup_library(root):
    root = validate_location(canonical_root(root))
    templates = Path(__file__).with_name('templates')
    if root == Path(__file__).resolve().parents[1]:
        raise SafetyError('资料目录必须与程序仓库分开')
    if linked(root):
        raise SafetyError('资料目录不能是链接')
    config_path = safe_path(root, '_整理规则/分类配置.json')
    agent_path = safe_path(root, 'AGENTS.md')
    rules_path = safe_path(root, '_整理规则/整理规则.md')
    shared_path=safe_path(root,'_整理规则/通用分类规则.md')
    version_path=safe_path(root,'_整理规则/通用规则版本.json')
    if any(p.exists() for p in (config_path, agent_path, rules_path,shared_path,version_path)):
        raise SafetyError('目录已含配置或整理规则，不覆盖；已有资料库请直接validate')
    policy = read_json(templates / 'classification-config.json')
    policy['library_root'] = str(root)
    categories = [relpath(category['root']) for category in policy['categories']]
    if any('/' in c for c in categories):
        raise SafetyError('初始化顶层类别无效')
    fixed = policy.get('initialization', {}).get('fixed_subdirectories', [])
    if not isinstance(fixed, list) or not all(isinstance(p, str) for p in fixed):
        raise SafetyError('固定子目录须为路径字符串列表')
    directories = list(dict.fromkeys(categories + [policy['review_path']] + fixed))
    # Validate every destination before creating any part of the empty skeleton.
    for relative in directories:
        normalized = relpath(relative)
        if normalized.split('/')[0] not in categories or '<' in normalized or '>' in normalized:
            raise SafetyError('初始化目录不属于已批准类别或含占位符')
        path = safe_path(root, normalized)
        if path.exists() and not path.is_dir():
            raise SafetyError('初始化目录被同名文件占用')
    for relative in directories:
        safe_path(root, relative).mkdir(parents=True, exist_ok=True)
    for destination, source in ((agent_path, 'AGENTS.md'), (rules_path, 'local-rules.md'),(shared_path,'classification.md')):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('x', encoding='utf-8') as stream:
            stream.write((templates / source).read_text(encoding='utf-8'))
    write_json(config_path, policy)
    from .rule_package import marker
    write_json(version_path,marker())
    return {'root': str(root), 'created': True, 'executable_rules': len(policy['executable_rules']),
            'fixed_subdirectories': fixed, 'original_files_touched': False}
