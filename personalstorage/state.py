from pathlib import Path
import sqlite3, os, json
from .common import digest,now,uid,SafetyError,write_json
from .paths import canonical_root,runtime_home

class State:
    def __init__(self,root,profile=None):
        self.root=canonical_root(root)
        self.profile=Path(profile) if profile else runtime_home()/digest(str(self.root).casefold())[:16]
        if self.profile.absolute().is_relative_to(self.root): raise SafetyError('运行数据库必须位于资料库之外')
        self.profile.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.profile/'state.sqlite3',timeout=10)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY,path TEXT UNIQUE COLLATE NOCASE,kind TEXT,data TEXT,seen_at TEXT);
        CREATE TABLE IF NOT EXISTS assignments(item_id TEXT PRIMARY KEY,version INTEGER,data TEXT);
        CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY,item_id TEXT,fingerprint TEXT,data TEXT,observed_at TEXT,UNIQUE(item_id,fingerprint));
        CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,item_id TEXT,context_key TEXT UNIQUE,status TEXT,data TEXT);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,run_id TEXT,status TEXT,data TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,operation_id TEXT,status TEXT,at TEXT,data TEXT);
        CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,data TEXT);
        CREATE TABLE IF NOT EXISTS observations(item_id TEXT PRIMARY KEY,status TEXT,checked_at TEXT,hash_checked INTEGER);
        CREATE TABLE IF NOT EXISTS ui_tasks(id TEXT PRIMARY KEY,action TEXT,page TEXT,status TEXT,started_at TEXT,finished_at TEXT,phase TEXT,summary TEXT,error TEXT,owner_pid INTEGER,title TEXT);
        CREATE INDEX IF NOT EXISTS ui_tasks_started ON ui_tasks(started_at);
        ''')
        identity={'root':str(self.root),'volume':self.root.stat().st_dev,'schema_version':1}
        old=self.get('identity')
        if old and old!=identity:
            try:same_root=os.path.samefile(old['root'],self.root)
            except (OSError,KeyError):same_root=False
            if not same_root or {k:v for k,v in old.items() if k!='root'}!={k:v for k,v in identity.items() if k!='root'}:
                raise SafetyError('资料库根、卷身份或数据库schema不匹配')
        self.set('identity',old or identity)

    def get(self,key,default=None):
        row=self.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def set(self,key,value):
        with self.db: self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value,ensure_ascii=False)))
    def item(self,path):
        r=self.db.execute('SELECT * FROM items WHERE path=?',(path,)).fetchone()
        return dict(r) if r else None
    def observe(self,path,kind,data):
        old=self.item(path); iid=old['id'] if old else uid()
        with self.db: self.db.execute('INSERT OR REPLACE INTO items VALUES (?,?,?,?,?)',(iid,path,kind,json.dumps(data,ensure_ascii=False),now()))
        return iid
    def event(self,opid,status,data):
        self.db.execute('INSERT INTO events(operation_id,status,at,data) VALUES(?,?,?,?)',(opid,status,now(),json.dumps(data,ensure_ascii=False)))
    def op(self,opid,status,data):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO operations VALUES(?,?,?,?,?)',(opid,data['run_id'],status,json.dumps(data,ensure_ascii=False),now()))
            self.event(opid,status,data)
        self.mirror_event(opid,status,data)
    def mirror_event(self,opid,status,data):
        # The database remains authoritative if a readable-log export is unavailable.
        try:
            p=self.root/'_整理规则/执行记录'/data['run_id']/'events.jsonl'
            p.parent.mkdir(parents=True,exist_ok=True)
            with p.open('a',encoding='utf-8') as f:
                f.write(json.dumps({'operation_id':opid,'status':status,'at':now(),'source':data.get('source'),'target':data.get('target')},ensure_ascii=False)+'\n')
                f.flush(); os.fsync(f.fileno())
        except OSError as e: self.set('log_export_warning',str(e))
    def add_review(self,iid,version,assignment,reason,status='pending'):
        key=digest([iid,version,assignment,reason])
        self.db.execute('INSERT OR IGNORE INTO reviews VALUES(?,?,?,?,?)',(uid(),iid,key,status,json.dumps({'version':version,'assignment':assignment,'reason':reason},ensure_ascii=False)))
    def register(self,op):
        # Metadata and queue registration commit together, after the filesystem move.
        prior=self.db.execute('SELECT status FROM operations WHERE id=?',(op['id'],)).fetchone()
        if prior and prior[0]=='committed': return
        old=self.item(op['source']); iid=old['id'] if old else uid()
        assignment={'destination_parent':str(Path(op['target']).parent).replace('\\','/'),'rule_id':op['rule_id'],'policy_hash':op['policy_hash'],'rule_fingerprint':op.get('rule_fingerprint'),'evidence':op['evidence'],'manual_locked':op.get('manual',False)}
        with self.db:
            if op['manifest']['kind']=='directory':
                members=self.db.execute('SELECT id,path FROM items').fetchall()
                for member in members:
                    if member['path'].casefold().startswith(op['source'].casefold()+'/'):
                        newpath=op['target']+member['path'][len(op['source']):]
                        self.db.execute('UPDATE items SET path=?,seen_at=? WHERE id=?',(newpath,now(),member['id']))
            self.db.execute('DELETE FROM items WHERE path=? AND id<>?',(op['target'],iid))
            self.db.execute('INSERT OR REPLACE INTO items VALUES(?,?,?,?,?)',(iid,op['target'],op['manifest']['kind'],json.dumps(op['manifest'],ensure_ascii=False),now()))
            self.db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?)',(uid(),iid,digest(op['manifest']),json.dumps(op['manifest'],ensure_ascii=False),now()))
            prev=self.db.execute('SELECT version FROM assignments WHERE item_id=?',(iid,)).fetchone()
            self.db.execute('INSERT OR REPLACE INTO assignments VALUES(?,?,?)',(iid,prev[0]+1 if prev else 1,json.dumps(assignment,ensure_ascii=False)))
            self.db.execute("UPDATE reviews SET status='superseded' WHERE item_id=? AND status<>'confirmed'",(iid,))
            self.add_review(iid,digest(op['manifest']),assignment,'classification_unresolved' if op.get('decision')=='review' else 'new_assignment','waiting_user' if op.get('decision')=='review' else 'pending')
            self.db.execute('INSERT OR REPLACE INTO operations VALUES(?,?,?,?,?)',(op['id'],op['run_id'],'committed',json.dumps(op,ensure_ascii=False),now()))
            self.event(op['id'],'committed',{'source':op['source'],'target':op['target']})
        self.mirror_event(op['id'],'committed',op)
    def snapshot(self,target):
        target=Path(target)
        if target.exists(): raise SafetyError('快照目标已存在')
        target.parent.mkdir(parents=True,exist_ok=True)
        dest=sqlite3.connect(target)
        try: self.db.backup(dest)
        finally: dest.close()
        return str(target)
    def close(self): self.db.close()
