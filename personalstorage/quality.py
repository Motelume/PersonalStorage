"""Evaluate against explicit labels; never infer a ground truth from predictions."""
import time
import statistics
from .common import SafetyError,safe_path,now
from .extraction import extract
from .tasks import checkpoint

def evaluate(app,cases,job=None,reference_source='user_supplied_labels'):
    if not isinstance(cases,list) or not 1<=len(cases)<=500:raise SafetyError('请提供1至500个人工标注样本')
    rows=[];times=[];correct=0;move_correct=0;move_total=0;expected_moves=0
    if any(not isinstance(c,dict) or not isinstance(c.get('path'),str) for c in cases) or len({c['path'] for c in cases})!=len(cases):raise SafetyError('评估样本路径不能重复或缺失')
    for index,case in enumerate(cases):
        checkpoint()
        if not isinstance(case,dict) or not isinstance(case.get('path'),str):raise SafetyError('样本需要资料库内相对路径')
        path=safe_path(app.root,case['path'])
        if case['path'].startswith(('_整理规则/','09_移动硬盘管理/')):raise SafetyError('管理目录不进入分类质量评估')
        expected=case.get('expected_target');decision=case.get('expected_decision','move' if expected else 'review')
        if decision not in ('move','review'):raise SafetyError('期望决定只支持move或review')
        if (decision=='move')!=bool(expected):raise SafetyError('move标签需要明确目标，review标签不能同时指定目标')
        if expected:
            app.policy.target(expected,path.name)
            if not safe_path(app.root,expected).is_dir():raise SafetyError('人工标注目标不存在')
            expected_moves+=1
        started=time.perf_counter();features={}
        try:
            before=path.stat();features=extract(path);actual=app.policy.classify(features,case.get('context'));after=path.stat()
            if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise SafetyError('提取期间文件变化')
        except (OSError,SafetyError,ValueError) as exc:actual={'decision':'error','reason':str(exc)}
        elapsed=(time.perf_counter()-started)*1000;times.append(elapsed)
        match=actual['decision']==decision and (decision!='move' or actual.get('target_parent')==expected)
        correct+=match
        if actual['decision']=='move':move_total+=1;move_correct+=match
        rows.append({'path':case['path'],'expected_target':expected,'expected_decision':decision,'actual':actual,'correct':match,'elapsed_ms':round(elapsed,2),'text_chars':len(features.get('text','')),'extraction_complete':features.get('complete',False),'warnings':features.get('warnings',[])})
        if job is not None:job['phase']=f'按人工标注评估 {index+1}/{len(cases)}'
    result={'at':now(),'samples':len(rows),'accuracy':correct/len(rows),'move_precision':move_correct/move_total if move_total else None,'move_recall':move_correct/expected_moves if expected_moves else None,'review_fraction':sum(r['actual']['decision']=='review' for r in rows)/len(rows),'median_ms':round(statistics.median(times),2),'p95_ms':round(sorted(times)[max(0,int(len(times)*.95)-1)],2),'rows':rows,'ground_truth':'user_supplied_labels','files_moved':False,'ai_called':False}
    result.update(ground_truth=reference_source,evaluation_mode='rule_only_without_assignments_or_ai',errors=sum(r['actual']['decision']=='error' for r in rows),text_extracted=sum(r['text_chars']>0 for r in rows),partial_extractions=sum(not r['extraction_complete'] for r in rows))
    app.state.set('quality_report',result);return result
