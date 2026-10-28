"""Private incremental full-text and bounded local LSA similarity search."""
import collections
import json
import math
import re
import sqlite3
import os
from pathlib import Path
from .common import safe_path,linked,digest,now,SafetyError
from .overview import SKIP
from .extraction import extract,signature
from .tasks import checkpoint


def database(state):
    db=sqlite3.connect(state.profile/'search.sqlite3',timeout=20);db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS documents(path TEXT PRIMARY KEY,stamp TEXT,text TEXT,source TEXT,status TEXT,warning TEXT,updated TEXT);
    CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(path UNINDEXED,text,tokenize='trigram');''')
    return db


def files(root):
    stack=[Path(root)];seen=0
    while stack:
        checkpoint();folder=stack.pop()
        try:
            for entry in folder.iterdir():
                if linked(entry) or entry.name.casefold() in SKIP or entry.name in ('_整理规则','09_移动硬盘管理') or entry.name.casefold()=='agents.md':continue
                if entry.is_dir():stack.append(entry)
                elif entry.is_file() and entry.suffix.lower() not in ('.lnk','.exe','.dll','.msi','.onnx','.sqlite3'):
                    seen+=1
                    if seen>100000:raise SafetyError('本次遍历超过十万文件预算，请选择更小范围')
                    yield entry
        except OSError as exc:raise SafetyError('目录读取未完成，本次不移除未见到的索引：'+str(folder)) from exc


def build(app,job=None,budget=500):
    if not isinstance(budget,int) or isinstance(budget,bool) or not 1<=budget<=10000:raise SafetyError('每批索引文件数须在1至10000')
    db=database(app.state);report={'updated':0,'cached':0,'errors':0,'remaining':0,'deleted':0,'complete':True};seen=set()
    app.state.set('search_report',{**report,'at':now(),'running':True})
    try:
        for path in files(app.root):
            checkpoint();relative=path.relative_to(app.root).as_posix();seen.add(relative);st=path.stat()
            stamp=digest([st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns,signature()])
            old=db.execute('SELECT stamp FROM documents WHERE path=?',(relative,)).fetchone()
            if old and old['stamp']==stamp:report['cached']+=1;continue
            if report['updated']>=budget:report['remaining']+=1;report['complete']=False;continue
            before=[st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns]
            try:
                result=extract(path);end=path.stat()
                if before!=[end.st_ino,end.st_size,end.st_mtime_ns,end.st_ctime_ns]:raise SafetyError('提取期间文件发生变化')
                text=result.get('text','');status='indexed' if text else 'no_text'
                with db:
                    db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?,?)',(relative,stamp,text,result.get('text_source','native'),status,'；'.join(result.get('warnings',[]))[:1000],now()))
                    db.execute('DELETE FROM document_fts WHERE path=?',(relative,));db.execute('INSERT INTO document_fts VALUES(?,?)',(relative,relative+'\n'+text))
                report['updated']+=1
            except (OSError,SafetyError,ValueError):report['errors']+=1;report['remaining']+=1;report['complete']=False
            if job is not None:job.update(phase=f"本地索引：更新{report['updated']}，复用{report['cached']}，未覆盖{report['remaining']}")
        for row in db.execute('SELECT path FROM documents').fetchall():
            if row['path'] not in seen:
                with db:db.execute('DELETE FROM documents WHERE path=?',(row['path'],));db.execute('DELETE FROM document_fts WHERE path=?',(row['path'],))
                report['deleted']+=1
        app.state.set('search_report',{**report,'at':now(),'running':False});app.state.set('search_freshness',None);return report
    except BaseException:
        app.state.set('search_report',{**report,'at':now(),'running':False,'complete':False,'interrupted':True});raise
    finally:db.close()


def status(app):
    db=database(app.state)
    try:return {'documents':db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],'with_text':db.execute("SELECT COUNT(*) FROM documents WHERE text<>''").fetchone()[0],'last':app.state.get('search_report'),'freshness':app.state.get('search_freshness'),'private':True}
    finally:db.close()


def freshness(app):
    db=database(app.state);seen=set();new=changed=0;sig=signature()
    try:
        old={r['path']:r['stamp'] for r in db.execute('SELECT path,stamp FROM documents')}
        for p in files(app.root):
            checkpoint();relative=p.relative_to(app.root).as_posix();seen.add(relative);st=p.stat();stamp=digest([st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns,sig])
            if relative not in old:new+=1
            elif old[relative]!=stamp:changed+=1
        result={'at':now(),'new':new,'changed':changed,'missing':len(set(old)-seen),'total':len(seen),'pending':new+changed,'metadata_only':True};app.state.set('search_freshness',result);return result
    finally:db.close()


def tokens(text):
    text=text.casefold();out=re.findall(r'[a-z0-9_]{2,}',text)
    for chunk in re.findall(r'[\u3400-\u9fff]+',text):
        out.extend(chunk[i:i+n] for n in (1,2,3) for i in range(len(chunk)-n+1))
    return collections.Counter(out)


def search(app,query,semantic=False,limit=50):
    if not isinstance(query,str) or not 1<=len(query.strip())<=200:raise SafetyError('请输入1至200字的检索内容')
    query=query.strip();db=database(app.state)
    try:
        if not semantic:
            if len(query)>=3:
                phrase='"'+query.replace('"','""')+'"'
                rows=db.execute('SELECT d.* FROM document_fts f JOIN documents d ON d.path=f.path WHERE document_fts MATCH ? ORDER BY rank LIMIT ?',(phrase,limit)).fetchall()
            else:rows=db.execute('SELECT * FROM documents WHERE instr(lower(path||char(10)||text),lower(?))>0 LIMIT ?',(query,limit)).fetchall()
            return {'rows':[present(r,query) for r in rows],'mode':'fulltext','indexed':status(app)['documents']}
        # Bounded CPU-only latent semantic analysis; no remote model or file upload.
        import numpy as np
        rows=db.execute("SELECT * FROM documents WHERE text<>'' ORDER BY updated DESC LIMIT 1500").fetchall()
        if not rows:return {'rows':[],'mode':'semantic_lsa','limited':False}
        counters=[tokens((r['path']+' '+r['text'])[:16000]) for r in rows];freq=collections.Counter()
        for c in counters:freq.update(c.keys())
        terms=[w for w,_ in freq.most_common(2500)];vocab={w:i for i,w in enumerate(terms)}
        if not terms:return {'rows':[],'mode':'semantic_lsa','limited':False}
        idf=np.array([math.log((len(rows)+1)/(freq[w]+1))+1 for w in terms],dtype=np.float32)
        x=np.zeros((len(rows),len(terms)),dtype=np.float32)
        def vector(c):
            v=np.zeros(len(terms),dtype=np.float32)
            for w,n in c.items():
                if w in vocab:v[vocab[w]]=1+math.log(n)
            return v*idf
        for i,c in enumerate(counters):x[i]=vector(c)
        x/=np.maximum(np.linalg.norm(x,axis=1,keepdims=True),1e-8)
        q=vector(tokens(query));rank=min(32,*x.shape)
        if rank>=2:
            rng=np.random.default_rng(42);y=x@rng.standard_normal((x.shape[1],rank)).astype(np.float32)
            for _ in range(2):y=x@(x.T@y)
            basis=np.linalg.qr(y,mode='reduced')[0];_,_,vt=np.linalg.svd(basis.T@x,full_matrices=False)
            docs=x@vt.T;needle=q@vt.T
        else:docs=x;needle=q
        scores=(docs@needle)/(np.maximum(np.linalg.norm(docs,axis=1)*np.linalg.norm(needle),1e-8))
        order=np.argsort(-scores)[:limit]
        return {'rows':[{**present(rows[i],query),'score':round(float(scores[i]),4)} for i in order if scores[i]>.05],
                'mode':'semantic_lsa','limited':len(rows)>=1500,'notice':'本地LSA相似度，最多最近1500篇；结果受语料规模影响，分数不是分类置信度'}
    finally:db.close()


def present(row,query):
    text=row['text'];start=max(0,text.casefold().find(query.casefold())-70)
    return {'path':row['path'],'excerpt':text[start:start+450],'source':row['source'],'warning':row['warning'],'updated':row['updated']}
