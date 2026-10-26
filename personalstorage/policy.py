from pathlib import Path
import re
from .common import read_json,digest,relpath,safe_path,SafetyError

REQUIRED_CONSTRAINTS=('preserve_names','preserve_content','no_delete','no_overwrite','no_dedup','same_volume_only','preserve_bundles','no_network')
FIELDS={'name','text','kind','context.task_id','context.purpose'}
OPS={'equals','contains','regex','in'}

class Policy:
    def __init__(self,root):
        self.root=Path(root).absolute()
        self.data=read_json(self.root/'_整理规则/分类配置.json')
        self.validate()
        self.hash=digest(self.data)
        self.categories={x['root'] for x in self.data['categories']}
    def validate(self):
        d=self.data
        if d.get('schema_version')!='3.0': raise SafetyError('需使用可执行schema 3.0，不能把自然语言配置当成脚本')
        if not all(d.get('constraints',{}).get(k) is True for k in REQUIRED_CONSTRAINTS): raise SafetyError('缺少必要的保护约束')
        cats=[x['root'] for x in d['categories']]
        if len(cats)!=len(set(cats)) or any('/' in relpath(x) for x in cats): raise SafetyError('顶层类别无效')
        if not d.get('policy_version'): raise SafetyError('缺少规则版本')
        ids=set()
        for rule in d['executable_rules']:
            if rule['id'] in ids: raise SafetyError('重复规则ID')
            ids.add(rule['id'])
            if not isinstance(rule.get('all'),list) or not rule['all']: raise SafetyError('规则必须有明确条件')
            for c in rule['all']:
                if set(c)!={'field','op','value'} or c['field'] not in FIELDS or c['op'] not in OPS: raise SafetyError('不支持的规则条件')
                if c['op']=='regex':
                    if not isinstance(c['value'],str) or len(c['value'])>256: raise SafetyError('正则超限')
                    re.compile(c['value'])
                if c['op']=='in' and not isinstance(c['value'],list): raise SafetyError('in需要列表')
            target=relpath(rule['target_parent'])
            if target.split('/')[0] not in cats: raise SafetyError('规则目标不是已批准类别')
            if target.startswith(('00_收件箱','09_移动硬盘管理','99_临时待整理')): raise SafetyError('普通规则不能隐式指向管理/暂存目录')
        for t in d.get('tasks',[]): relpath(t['path'])

    def target(self,parent,name,allow_archived=False,review=False):
        parent=relpath(parent)
        top=parent.split('/')[0]
        if top not in self.categories or top in ('09_移动硬盘管理',): raise SafetyError('目标类别受保护')
        if top=='00_收件箱' and not (review and parent==self.data['review_path']): raise SafetyError('不能将收件箱当最终目的地')
        if any(s.endswith(('（已归档）','（已完成）')) for s in parent.split('/')) and not allow_archived:
            raise SafetyError('补充已完成/归档任务需明确授权')
        p=safe_path(self.root,parent+'/'+name)
        return p.relative_to(self.root).as_posix()

    def decision_fingerprint(self,rule_ids):
        ids=rule_ids.split(',')
        relevant=[r for r in self.data['executable_rules'] if r['id'] in ids]
        return digest({'constraints':self.data['constraints'],'rules':relevant,'context_tasks':self.data.get('tasks',[]) if 'context_task' in ids else []})

    @staticmethod
    def matches(c,features):
        v=features.get(c['field'],''); expect=c['value']
        if c['op']=='equals': return v==expect
        if c['op']=='contains': return str(expect).casefold() in str(v).casefold()
        if c['op']=='regex': return re.search(expect,str(v),re.I) is not None
        if c['op']=='in': return v in expect
        return False

    def classify(self,features,context=None):
        f=dict(features)
        f.update({'context.'+k:v for k,v in (context or {}).items()})
        if (context or {}).get('task_id'):
            tasks=[t for t in self.data.get('tasks',[]) if t['id']==context['task_id']]
            if len(tasks)!=1: return {'decision':'review','reason':'任务ID未知或不唯一'}
            task=tasks[0]
            if not safe_path(self.root,task['path']).is_dir(): return {'decision':'review','reason':'登记任务的实际目录已不存在，需核对新位置；不能自动重建'}
            if task.get('storage')!='active' or task.get('lifecycle') in ('archived','completed'):
                return {'decision':'review','reason':'任务位于暂存/离线位置或已归档，需确定补充意图'}
            return {'decision':'move','target_parent':task['path'],'rule_id':'context_task','evidence':'本次明确的任务投递上下文'}
        matches=[r for r in self.data['executable_rules'] if all(self.matches(c,f) for c in r['all'])]
        if not matches: return {'decision':'review','reason':'无明确可执行规则；需人工或AI复核'}
        # A lower number only selects an explicitly defined decision level.
        level=min(r.get('level',100) for r in matches)
        selected=[r for r in matches if r.get('level',100)==level]
        targets={r['target_parent'] for r in selected}
        if len(targets)!=1: return {'decision':'review','reason':'同层规则指向不同目标','candidates':sorted(targets)[:3]}
        return {'decision':'move','target_parent':selected[0]['target_parent'],'rule_id':','.join(r['id'] for r in selected),'evidence':'已发布明确条件全部匹配'}
