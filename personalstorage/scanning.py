"""Read-only inbox suggestions; integrity manifests are built only for previews."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import stat
import time

from .common import SafetyError, digest, linked, safe_path, uid, now
from .discovery import discover, eligible_name
from .extraction import extract,signature
from .planner import in_scope


def metadata(path):
    """Cheap, link-safe change detector. This is deliberately NOT a content hash."""
    path=Path(path);entries=[];files=0
    def visit(p,relative):
        nonlocal files
        if linked(p):raise SafetyError('拒绝链接、junction或重解析点')
        s=p.stat(follow_symlinks=False)
        if stat.S_ISREG(s.st_mode):
            if not eligible_name(p.name):raise SafetyError('资料包含未完成/锁定文件')
            files+=1;kind='file'
        elif stat.S_ISDIR(s.st_mode):kind='directory'
        else:raise SafetyError('不支持特殊文件')
        entries.append((relative,kind,s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns))
        if kind=='directory':
            with os.scandir(p) as children:
                for child in children:visit(Path(child.path),relative+'/'+child.name)
    visit(path,'')
    return {'fingerprint':digest(sorted(entries)),'kind':entries[0][1],'file_count':files}


def scan(root,policy,cache,*,include_root=False,stable_seconds=1,progress=None):
    root=Path(root);sources=discover(root,include_root)
    first={};errors={};started=time.monotonic()
    for source in sources:
        try:
            if not in_scope(source,include_root):raise SafetyError('源不在本次授权范围')
            first[source]=metadata(safe_path(root,source))
        except (OSError,SafetyError) as exc:errors[source]=str(exc)
    # All candidates share one observation interval instead of one sleep each.
    if first:time.sleep(max(0,stable_seconds-(time.monotonic()-started)))
    targets=set();rows=[];hits=0;extractor_signature=signature()
    def inspect(source):
        try:
            if source in errors:raise SafetyError(errors[source])
            path=safe_path(root,source);current=metadata(path)
            if current!=first[source]:raise SafetyError('文件仍在变化')
            if not current['file_count']:raise SafetyError('空文件夹保持原位')
            key=(source,current['fingerprint'],policy.hash,extractor_signature)
            cached=cache.get(key)
            features=cached if cached is not None else extract(path)
            if cached is None:
                if metadata(path)!=current:raise SafetyError('读取时源文件变化')
                if features.get('ocr',{}).get('status') not in ('error','timeout','unavailable'):cache[key]=features
            decision=policy.classify(features)
            row={'source':source,'kind':current['kind'],'file_count':current['file_count'],
                 'text_source':features.get('text_source','native' if features.get('text') else 'none'),'ocr':({**features['ocr'],'cache_hit':True} if cached is not None and features.get('ocr') else features.get('ocr')),'scan_fingerprint':current['fingerprint'],'integrity':'metadata_only','decision':decision['decision']}
            if decision['decision']=='review':
                row.update(reason=decision['reason'],candidates=decision.get('candidates',[]),rule_id='unresolved',evidence=decision['reason'])
                row['target']=policy.target(policy.data['review_path'],path.name,review=True)
            else:
                row.update(decision);row['target']=policy.target(decision['target_parent'],path.name,review=False)
            return row,cached is not None
        except (OSError,SafetyError) as exc:return {'source':source,'decision':'skip','reason':str(exc)},False
    with ThreadPoolExecutor(max_workers=4,thread_name_prefix='inbox-read') as pool:
        for index,(row,hit) in enumerate(pool.map(inspect,sources),1):
            hits+=hit;target=row.get('target')
            if target:
                if target==row['source']:row.update(decision='skip',reason='已在目标位置')
                elif target.casefold() in targets or safe_path(root,target).exists():row.update(decision='conflict',reason='目标同名或本批目标重名，保留源原位')
                targets.add(target.casefold())
            rows.append(row)
            if progress:progress(index,len(sources))
    if len(cache)>4096:cache.clear()
    return {'rows':rows,'scanned_at':now(),'elapsed_ms':round((time.monotonic()-started)*1000),'cache_hits':hits,'content_hashed':any(row.get('ocr') is not None for row in rows)}
