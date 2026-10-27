"""Reproducible synthetic classification/index benchmark; not a real-data accuracy claim."""
import sys
import os
import tempfile
import json
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_core import config
from personalstorage.common import write_json
from personalstorage.service import Application
from personalstorage.quality import evaluate
from personalstorage import searching

def main(output):
    with tempfile.TemporaryDirectory(prefix='personalstorage-quality-') as tmp:
        base=Path(tmp);os.environ['PERSONALSTORAGE_HOME']=str(base/'machine');root=base/'library';(root/'01_课程学习/测试课程').mkdir(parents=True);(root/'00_收件箱').mkdir()
        cfg=config();cfg['executable_rules'][0]['all']=[{'field':'text','op':'contains','value':'course'}];write_json(root/'_整理规则/分类配置.json',cfg);cases=[]
        for i in range(100):
            relative='00_收件箱/sample-'+str(i)+'.txt';known=i%4!=0;(root/relative).write_text('course reference controls '+str(i) if known else 'ambiguous unassigned material '+str(i),encoding='utf-8')
            cases.append({'path':relative,'expected_target':'01_课程学习/测试课程' if known else None,'expected_decision':'move' if known else 'review'})
        app=Application(root,base/'state')
        try:
            report=evaluate(app,cases);started=time.perf_counter();first=searching.build(app,budget=1000);cold=time.perf_counter()-started;started=time.perf_counter();warm=searching.build(app,budget=1000);cached=time.perf_counter()-started
            result={k:v for k,v in report.items() if k!='rows'};result.update(dataset='100 synthetic labelled text files; not user-data accuracy',index_cold_seconds=round(cold,3),index_warm_seconds=round(cached,3),warm_cache_hits=warm['cached'],cold_indexed=first['updated'])
            Path(output).parent.mkdir(parents=True,exist_ok=True);write_json(output,result)
            if result['accuracy']!=1 or warm['cached']!=100:raise AssertionError('Synthetic acceptance regression')
            print(json.dumps(result,ensure_ascii=False))
        finally:app.close()

if __name__=='__main__':main(sys.argv[1] if len(sys.argv)>1 else '.local/quality-acceptance.json')
