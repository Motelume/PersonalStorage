"""Evaluate against explicit labels; never infer a ground truth from predictions."""
import time
import statistics
from .common import SafetyError,safe_path,now
from .extraction import extract
from .tasks import checkpoint

def evaluate(app,cases,job=None):
    if not isinstance(cases,list) or not 1<=len(cases)<=500:raise SafetyError('请提供1至500个人工标注样本')
    rows=[];times=[];correct=0;move_correct=0;move_total=0;expected_moves=0
    for index,case in enumerate(cases):
        checkpoint()
        if not isinstance(case,dict) or not isinstance(case.get('path'),str):raise SafetyError('样本需要资料库内相对路径')
        path=safe_path(app.root,case['path'])
        if case['path'].startswith(('_整理规则/','09_移动硬盘管理/')):raise SafetyError('管理目录不进入分类质量评估')
        expected=case.get('expected_target');decision=case.get('expected_decision','move' if expected else 'review')
        if decision not in ('move','review'):raise SafetyError('期望决定只支持move或review')
        if expected:
            app.policy.target(expected,path.name)
            if not safe_path(app.root,expected).is_dir():raise SafetyError('人工标注目标不存在')
            expected_moves+=1
        started=time.perf_counter();features=extract(path);actual=app.policy.classify(features,case.get('context'));elapsed=(time.perf_counter()-started)*1000;times.append(elapsed)
        match=actual['decision']==decision and (decision!='move' or actual.get('target_parent')==expected)
        correct+=match
        if actual['decision']=='move':move_total+=1;move_correct+=match
        rows.append({'path':case['path'],'expected_target':expected,'expected_decision':decision,'actual':actual,'correct':match,'elapsed_ms':round(elapsed,2)})
        if job is not None:job['phase']=f'按人工标注评估 {index+1}/{len(cases)}'
    result={'at':now(),'samples':len(rows),'accuracy':correct/len(rows),'move_precision':move_correct/move_total if move_total else None,'move_recall':move_correct/expected_moves if expected_moves else None,'review_fraction':sum(r['actual']['decision']=='review' for r in rows)/len(rows),'median_ms':round(statistics.median(times),2),'p95_ms':round(sorted(times)[max(0,int(len(times)*.95)-1)],2),'rows':rows,'ground_truth':'user_supplied_labels','files_moved':False,'ai_called':False}
    app.state.set('quality_report',result);return result
