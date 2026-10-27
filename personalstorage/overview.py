"""Read-only library navigation; graph relationships never mutate original files."""
from pathlib import Path
import heapq
import json
import math
import os
import re
from .common import SafetyError,safe_path,linked,now,uid,relpath

SKIP={'.git','.venv','.venv-desktop','venv','node_modules','__pycache__','$recycle.bin','system volume information'}

def path_at(root,relative):
    return safe_path(root,relative) if relative else Path(root)

def describe(root,relative):
    p=path_at(root,relative)
    if linked(p):raise SafetyError('链接或重解析点不在目录浏览范围内')
    st=p.stat();kind='directory' if p.is_dir() else 'file'
    return {'path':relative,'name':p.name,'kind':kind,'identity':[st.st_dev,st.st_ino],
            'size':st.st_size if kind=='file' else None,'modified':st.st_mtime,'absolute_path':str(p),
            'managed':relative=='_整理规则' or relative.startswith('_整理规则/'),'available':True}

def children(root,relative='',files=False,offset=0,limit=200):
    if isinstance(offset,bool) or not isinstance(offset,int) or not 0<=offset<=20000:raise SafetyError('目录分页范围无效')
    parent=describe(root,relative);folder=path_at(root,relative)
    if parent['kind']!='directory':raise SafetyError('请选择文件夹')
    def eligible(entry):
        if entry.name.casefold() in SKIP or entry.is_symlink():return False
        if not files and not entry.is_dir(follow_symlinks=False):return False
        return True
    with os.scandir(folder) as entries:
        picked=heapq.nsmallest(offset+limit+1,(e for e in entries if eligible(e)),key=lambda e:(not e.is_dir(follow_symlinks=False),e.name.casefold()))
    result=[];unavailable=0
    for entry in picked[offset:offset+limit]:
        child=(relative+'/' if relative else '')+entry.name
        try:result.append(describe(root,child))
        except (OSError,SafetyError):unavailable+=1
    if describe(root,relative)['identity']!=parent['identity']:raise SafetyError('目录已变化，请刷新')
    return {'parent':parent,'nodes':result,'offset':offset,'next_offset':offset+limit if len(picked)>offset+limit else None,'unavailable':unavailable,'observed_at':now()}

def check_node(root,obj):
    if not obj.get('overview'):raise SafetyError('请选择目录总览中的节点')
    node=describe(root,obj['path'])
    if node['identity']!=obj.get('node_identity'):raise SafetyError('原位置已变化，请刷新或重新定位')
    return node

def search(root,query,files,stop,progress=None):
    if not isinstance(query,str) or not 1<=len(query.strip())<=120:raise SafetyError('请输入1至120字的名称或路径')
    needle=query.strip().casefold();stack=[''];rows=[];scanned=0;errors=0;limited=False
    while stack and not stop.is_set():
        relative=stack.pop();folder=path_at(root,relative)
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if stop.is_set():break
                    scanned+=1
                    if scanned>100000:limited=True;break
                    if entry.name.casefold() in SKIP or entry.is_symlink():continue
                    child=(relative+'/' if relative else '')+entry.name
                    try:
                        candidate=path_at(root,child)
                        if linked(candidate):continue
                        directory=entry.is_dir(follow_symlinks=False)
                        if directory and child!='_整理规则':stack.append(child)
                        if (directory or files) and needle in child.casefold():rows.append(describe(root,child))
                    except (OSError,SafetyError):errors+=1
                    if len(rows)>=500:limited=True;break
                    if progress and scanned%100==0:progress(scanned)
            if limited:break
        except (OSError,SafetyError):errors+=1
    return {'nodes':rows,'scanned':scanned,'errors':errors,'limited':limited,'stopped':stop.is_set(),'observed_at':now()}

def preferences(state):
    return state.get('overview_preferences',{'mode':'graph','motion':True,'files':False,'manual':True,'positions':{}})

def save_preferences(root,state,body):
    patch={k:v for k,v in body.items() if k in ('mode','motion','files','manual','positions','expanded','focus','selected','camera')}
    if 'mode' in patch and patch['mode'] not in ('graph','tree'):raise SafetyError('视图类型无效')
    for key in ('motion','files','manual'):
        if key in patch and not isinstance(patch[key],bool):raise SafetyError('显示设置无效')
    for key in ('focus','selected'):
        if key in patch:
            if not isinstance(patch[key],str):raise SafetyError('节点路径无效')
            path_at(root,patch[key])
    if 'expanded' in patch:
        if not isinstance(patch['expanded'],list) or len(patch['expanded'])>500:raise SafetyError('展开状态过大')
        for relative in patch['expanded']:path_at(root,relative)
    if 'positions' in patch:
        if not isinstance(patch['positions'],dict) or len(patch['positions'])>1000:raise SafetyError('布局节点过多')
        for relative,point in patch['positions'].items():
            path_at(root,relative)
            if not isinstance(point,dict) or set(point)!={'x','y'} or any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or abs(n)>100000 for n in point.values()):raise SafetyError('节点坐标无效')
    if 'camera' in patch:
        camera=patch['camera']
        if not isinstance(camera,dict) or set(camera)!={'x','y','zoom'} or any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or abs(n)>100000 for n in camera.values()) or not .2<=camera['zoom']<=2.5:raise SafetyError('画布位置无效')
    with state.db:
        state.db.execute('BEGIN IMMEDIATE')
        data=preferences(state);data.update(patch)
        state.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('overview_preferences',json.dumps(data,ensure_ascii=False)))
    return {'saved':True,'original_files_touched':False}

def links(root,state):
    result=[]
    for row in state.db.execute('SELECT * FROM graph_links ORDER BY updated_at,id'):
        data=json.loads(row['data']);endpoints={}
        for side in ('source','target'):
            old=data[side]
            try:
                node=describe(root,old['path'])
                if node['identity']!=old['identity']:raise SafetyError('原位置的资料身份已变化')
            except (OSError,SafetyError):node={**old,'name':Path(old['path']).name,'available':False,'error':'关联端点需重新定位'}
            endpoints[side]=node
        result.append({'id':row['id'],'label':data['label'],'note':data.get('note',''),'updated_at':row['updated_at'],**endpoints})
    return result

def save_link(root,state,source,target,body):
    first=check_node(root,source);second=check_node(root,target)
    if first['identity']==second['identity']:raise SafetyError('不能把节点关联到自身')
    label=body.get('label','');note=body.get('note','');ident=body.get('link_id') or uid()
    if not isinstance(label,str) or not 1<=len(label.strip())<=60 or not isinstance(note,str) or len(note)>500:raise SafetyError('关系说明需1至60字，备注最多500字')
    if not re.fullmatch('[0-9a-f]{32}',ident):raise SafetyError('关联标识无效')
    with state.db:
        state.db.execute('BEGIN IMMEDIATE')
        existing=state.db.execute('SELECT id FROM graph_links WHERE id=?',(ident,)).fetchone()
        if body.get('link_id') and not existing:raise SafetyError('关联已变化，请刷新')
        if not existing and state.db.execute('SELECT COUNT(*) FROM graph_links').fetchone()[0]>=500:raise SafetyError('当前资料库最多保留500条手动关联')
        data={'source':{k:first[k] for k in ('path','identity')},'target':{k:second[k] for k in ('path','identity')},'label':label.strip(),'note':note}
        state.db.execute('INSERT OR REPLACE INTO graph_links VALUES(?,?,?)',(ident,json.dumps(data,ensure_ascii=False),now()))
    return {'id':ident,'saved':True,'original_files_touched':False}

def delete_link(state,ident):
    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):raise SafetyError('关联标识无效')
    with state.db:state.db.execute('DELETE FROM graph_links WHERE id=?',(ident,))
    return {'removed':True,'original_files_touched':False}
