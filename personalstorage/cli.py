import argparse,json,sys,time,sqlite3
from pathlib import Path
from .service import Application
from .common import read_json,write_json,manifest,digest,now,uid,SafetyError,instance_lock,safe_path
from .discovery import baseline
from .recovery import undo
from . import review,reports
from .config import default_root

def main(argv=None):
    p=argparse.ArgumentParser(description='PersonalStorage 本地整理工具（本机前端、无模型联网）')
    p.add_argument('--root',default=default_root()); p.add_argument('--profile')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('setup',help='从通用模板创建空资料库，不覆盖已有规则')
    sub.add_parser('init'); sub.add_parser('validate'); sub.add_parser('status'); sub.add_parser('pending'); sub.add_parser('recover'); sub.add_parser('operations')
    s=sub.add_parser('check'); s.add_argument('--full-hash',action='store_true')
    s=sub.add_parser('plan'); s.add_argument('--include-root',action='store_true'); s.add_argument('--include-review',action='store_true'); s.add_argument('--source',action='append'); s.add_argument('--allow-existing',action='store_true'); s.add_argument('--allow-archived',action='store_true'); s.add_argument('--decisions'); s.add_argument('--approve-decisions',action='store_true'); s.add_argument('--stable-seconds',type=float,default=1)
    s.add_argument('--task',help='本次明确指定的现有任务ID')
    s=sub.add_parser('apply'); s.add_argument('plan_id'); s.add_argument('--approve',action='store_true',required=True)
    s=sub.add_parser('confirm'); s.add_argument('source'); s.add_argument('target_parent'); s.add_argument('--evidence',required=True); s.add_argument('--allow-existing',action='store_true'); s.add_argument('--allow-archived',action='store_true'); s.add_argument('--execute',action='store_true'); s.add_argument('--approve',action='store_true',required=True)
    s=sub.add_parser('search'); s.add_argument('query'); s.add_argument('--offline',action='store_true')
    s=sub.add_parser('report'); s.add_argument('--output')
    s=sub.add_parser('pause'); s.add_argument('--resume',action='store_true')
    s=sub.add_parser('undo'); s.add_argument('operation_id'); s.add_argument('--approve',action='store_true',required=True)
    s=sub.add_parser('review-export'); s.add_argument('--output',required=True); s.add_argument('--with-content',action='store_true'); s.add_argument('--limit',type=int,default=30)
    s=sub.add_parser('review-import'); s.add_argument('input')
    s=sub.add_parser('snapshot'); s.add_argument('--output',required=True)
    s=sub.add_parser('restore-state'); s.add_argument('snapshot'); s.add_argument('--approve',action='store_true',required=True)
    s=sub.add_parser('authorize'); s.add_argument('--include-root',action='store_true'); s.add_argument('--approve',action='store_true',required=True)
    sub.add_parser('revoke')
    s=sub.add_parser('watch'); s.add_argument('--interval',type=float,default=60); s.add_argument('--once',action='store_true')
    s=sub.add_parser('serve'); s.add_argument('--port',type=int,default=0); s.add_argument('--no-browser',action='store_true')
    a=p.parse_args(argv); app=None
    try:
        if a.command=='setup':
            from .setup import setup_library
            print(json.dumps(setup_library(a.root),ensure_ascii=False,indent=2))
            return 0
        if a.command=='serve':
            from .web import serve
            serve(a.root,a.profile,a.port,not a.no_browser)
            return 0
        app=Application(a.root,a.profile); state=app.state; c=a.command
        if c=='validate': result={'valid':True,'policy_version':app.policy.data['policy_version'],'policy_hash':app.policy.hash}
        elif c=='init': result=baseline(app.root,state)
        elif c=='status': result=app.status()
        elif c=='pending': result=reports.pending(state)
        elif c=='operations': result=reports.operations(state)
        elif c=='plan':
            decisions=read_json(a.decisions) if a.decisions else None
            result=app.plan(include_root=a.include_root,include_review=a.include_review,sources=a.source,decisions=decisions,approve_decisions=a.approve_decisions,allow_existing=a.allow_existing,allow_archived=a.allow_archived,stable_seconds=max(a.stable_seconds,0),context={'task_id':a.task} if a.task else None)
        elif c=='apply': result=app.execute(a.plan_id)
        elif c=='confirm':
            from .planner import in_scope
            if not in_scope(a.source,True,True,a.allow_existing): raise SafetyError('源不在明确授权范围')
            m=manifest(safe_path(app.root,a.source))
            result=app.plan(include_root=True,include_review=True,sources=[a.source],decisions={a.source:{'target_parent':a.target_parent,'manifest_hash':digest(m),'evidence':a.evidence}},approve_decisions=True,allow_existing=a.allow_existing,allow_archived=a.allow_archived)
            if a.execute: result=app.execute(result['id'])
        elif c=='recover': result=app.recover()
        elif c=='check':
            from .maintenance import reconcile
            result=reconcile(app.root,state,app.policy,a.full_hash)
        elif c=='undo': result=undo(app.root,state,app.policy,a.operation_id)
        elif c=='pause': state.set('paused',not a.resume); result={'paused':not a.resume}
        elif c=='search': result=reports.offline_search(app.root,a.query) if a.offline else reports.search(state,a.query)
        elif c=='report':
            text=reports.markdown(state)
            if a.output:
                target=Path(a.output)
                if target.exists(): raise SafetyError('报告目标已存在')
                target.parent.mkdir(parents=True,exist_ok=True); target.write_text(text,encoding='utf-8')
                result={'output':str(target)}
            else: print(text); return 0
        elif c=='review-export': result=review.export_tasks(app.root,state,app.policy,a.output,a.with_content,a.limit)
        elif c=='review-import': result=review.import_results(app.root,state,app.policy,read_json(a.input))
        elif c=='snapshot':
            from .snapshot import management_snapshot
            result=management_snapshot(app.root,state,app.policy,a.output)
        elif c=='restore-state':
            with instance_lock(state.profile):
                backup=Path(a.snapshot)
                check=sqlite3.connect(f'file:{backup.as_posix()}?mode=ro',uri=True)
                try:
                    identity=check.execute("SELECT value FROM settings WHERE key='identity'").fetchone()
                    if not identity or json.loads(identity[0])!=state.get('identity'): raise SafetyError('快照资料库/卷/schema不匹配')
                    if check.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise SafetyError('快照完整性检查失败')
                    prior=state.profile/'backups'/(uid()+'.sqlite3'); state.snapshot(prior)
                    state.db.close()
                    dest=sqlite3.connect(state.profile/'state.sqlite3')
                    try: check.backup(dest)
                    finally: dest.close()
                finally: check.close()
            result={'restored':True,'previous_state_snapshot':str(prior),'original_files_touched':False,'next_step':'运行recover并核对实际文件；恢复状态不移动原资料'}
        elif c=='authorize':
            auth={'id':uid(),'policy_hash':app.policy.hash,'root':str(app.root),'include_root':a.include_root,'at':now()}
            state.set('automation',auth); result={'authorized':auth,'started':False}
        elif c=='revoke': state.set('automation',None); result={'authorized':False}
        elif c=='watch':
            auth=state.get('automation')
            if not auth or auth['policy_hash']!=app.policy.hash: raise SafetyError('未持续授权或规则版本已变化；先authorize --approve')
            app.recover()
            while True:
                current=state.get('automation')
                if not current or current!=auth: break
                if not state.get('paused',False):
                    from .policy import Policy
                    if Policy(app.root).hash!=auth['policy_hash']: raise SafetyError('规则已变化，停止后台处理')
                    plan=app.plan(include_root=auth['include_root'],automatic=True)
                    if plan['actions']: print(json.dumps(app.execute(plan['id']),ensure_ascii=False),flush=True)
                if a.once: break
                time.sleep(max(a.interval,1))
            result={'stopped':True}
        print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
    except KeyboardInterrupt: print('已停止；未完成动作请运行recover',file=sys.stderr); return 130
    except (SafetyError,OSError,ValueError,sqlite3.Error,KeyError) as e:
        print(json.dumps({'error':str(e),'type':type(e).__name__},ensure_ascii=False),file=sys.stderr); return 2
    finally:
        if app: app.close()

if __name__=='__main__': raise SystemExit(main())
