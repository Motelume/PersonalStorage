from pathlib import Path
from .common import digest,now,write_json,SafetyError

def management_snapshot(root,state,policy,target):
    target=Path(target); meta=target.with_name(target.name+'.metadata.json')
    if target.exists() or meta.exists(): raise SafetyError('快照或配套说明已存在，不覆盖')
    documents={}
    for name in ['AGENTS.md','_整理规则/分类配置.json','_整理规则/整理规则.md','_整理规则/执行协议.md','_整理规则/工具使用说明.md','_整理规则/AI整理指令.md']:
        p=Path(root)/name
        if p.is_file(): documents[name]=p.read_text(encoding='utf-8-sig')
    state.snapshot(target)
    write_json(meta,{'schema_version':1,'at':now(),'identity':state.get('identity'),'policy_hash':policy.hash,'policy_version':policy.data['policy_version'],'documents':documents,'original_contents_included':False,'recovery':'先validate；restore-state只恢复数据库并先备份当前状态；再recover/check核对文件。规则文本在documents内保留供人工恢复，不自动改写当前规则。'})
    return {'database':str(target),'metadata_and_rule_texts':str(meta),'contains_original_file_contents':False}
