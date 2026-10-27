"""Bounded boolean conditions with three-valued missing-feature semantics."""
from .common import SafetyError


def expression(rule):return rule.get('when',{'all':rule.get('all',[])})


def leaves(node,depth=0):
    if depth>6 or not isinstance(node,dict):raise SafetyError('条件结构无效或嵌套超过6层')
    if 'field' in node:return [node]
    if len(node)!=1 or next(iter(node)) not in ('all','any','not'):raise SafetyError('条件组必须是全部、任一或排除')
    op=next(iter(node));children=[node[op]] if op=='not' else node[op]
    if not isinstance(children,list) or not children or len(children)>32:raise SafetyError('条件组需包含1至32个子条件')
    result=[leaf for child in children for leaf in leaves(child,depth+1)]
    if len(result)>64:raise SafetyError('每条规则最多64个条件')
    return result


def evaluate(node,features,matcher):
    from .tasks import checkpoint
    checkpoint()
    if 'field' in node:
        value=matcher(node,features) if node['field'] in features else None
        trace={**node,'present':node['field'] in features,'matched':value is True,'truth':value,'actual':str(features.get(node['field'],''))[:180]}
        return value,[trace],{'condition':node,'truth':value}
    op=next(iter(node));children=[node[op]] if op=='not' else node[op]
    parts=[evaluate(child,features,matcher) for child in children];values=[v[0] for v in parts]
    if op=='not':value=None if values[0] is None else not values[0]
    elif op=='all':value=False if False in values else None if None in values else True
    else:value=True if True in values else None if None in values else False
    return value,[leaf for part in parts for leaf in part[1]],{'op':op,'truth':value,'children':[p[2] for p in parts]}
