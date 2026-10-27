"""Seven-day UI task history, separate from permanent file-operation auditing."""
from datetime import datetime,timedelta
import json
import os
from .common import now

PAGE={'scan':'inbox','detail':'inbox','preview':'inbox','execute':'inbox','keep':'review',
      'resolve-external':'review','review-export':'review','review-import':'review',
      'recover':'history','undo-preview':'history','undo':'history','snapshot':'settings'}
TITLES={'scan':'扫描收件箱','detail':'读取资料详情','preview':'生成移动预览','execute':'执行移动计划',
        'keep':'保留当前归属','resolve-external':'标记自行处理','review-export':'导出审核任务',
        'review-import':'导入审核结果','recover':'检查恢复记录','undo-preview':'检查撤销','undo':'撤销移动',
        'snapshot':'导出管理快照','ai-prepare':'准备AI发送预览','ai-run':'DeepSeek审核',
        'ai-adopt':'采用AI建议并预览','ai-test':'测试DeepSeek连接','ai-configure':'保存AI设置',
        'ai-clear-key':'移除AI密钥','pause':'调整执行暂停','authorize':'保存后台授权',
        'revoke':'撤销后台授权','watch-start':'启动后台','watch-stop':'停止后台',
        'inbox-add':'添加收件箱','inbox-toggle':'调整收件箱监测','inbox-mode':'调整归档方式','inbox-remove':'移除收件箱来源','library-audit':'全库位置复核',
        'cleanup-preview':'核对待清理原件','cleanup-execute':'清理已校验原件','overview-search':'目录总览名称搜索',
        'ocr-configure':'保存本地OCR配置','ocr-clear-cache':'清理识别缓存','ocr-test':'离线OCR自检'}
TITLES.update({'rules-save':'保存规则草稿','rules-reset':'从现行或历史规则建立草稿','rules-validate':'检查规则草稿',
               'rules-simulate':'本地规则样本试跑','rules-preview':'预览规则发布差异','rules-publish':'发布分类规则','rules-reading':'生成规则阅读版'})

TITLES.update({'wb-index': '更新本地正文索引', 'wb-search': '全文与语义检索', 'wb-backup': '生成管理快照', 'wb-restore-preview': '检查管理恢复', 'wb-restore': '恢复管理状态', 'wb-original-preview': '检查原资料备份', 'wb-original-backup': '校验备份原资料', 'wb-original-restore-preview': '检查原资料恢复', 'wb-original-restore': '恢复原资料到新目录', 'wb-lifecycle-preview': '检查任务目录状态', 'wb-lifecycle': '变更任务目录状态', 'wb-package-preview': '比较通用规则包', 'wb-package-update': '更新通用规则包', 'wb-ai-preview': '预览AI候选输入', 'wb-ai-run': '生成AI候选', 'wb-ai-accept-rule': '采用规则候选到草稿', 'wb-ai-accept-link': '确认目录关联', 'wb-quality': '评估人工标注样本', 'wb-recognition-install': '准备本地识别组件', 'wb-recognize': '增强本地识别', 'wb-recognition-accept': '采用已核对的识别文字', 'wb-diagnostics': '运行本机诊断', 'wb-update-preview': '检查测试版更新', 'wb-update-stage': '校验更新安装包', 'wb-update-launch': '打开测试版安装器', 'wb-rollback-preview': '检查程序版本回退', 'wb-rollback': '准备程序版本回退'})


def begin(state,ident,action,page=None):
    at=now();owner=page if page in ('inbox','inboxes','review','history','settings','ai','overview') else PAGE.get(action,'ai' if action.startswith('ai-') else 'settings')
    cutoff=(datetime.fromisoformat(at)-timedelta(days=7)).isoformat(timespec='seconds')
    with state.db:
        state.db.execute("DELETE FROM ui_tasks WHERE finished_at IS NOT NULL AND finished_at<?",(cutoff,))
        state.db.execute('INSERT INTO ui_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (ident,action,owner,'queued',at,None,'等待处理',None,None,os.getpid(),TITLES.get(action,action)))


def update(state,ident,status,*,phase=None,result=None,error=None):
    summary=None
    if result is not None:
        if isinstance(result,dict) and 'rows' in result:summary=f"扫描完成，共{len(result['rows'])}项；{result.get('elapsed_ms',0)}毫秒"
        elif isinstance(result,dict) and 'approved' in result:summary=f"复核状态：{result['status']}；位置合理{result['approved']}项，建议调整{result['suggestions']}项，待补充{result['needs_input']}项；复用{result['cache_hits']}项"
        elif isinstance(result,dict) and 'results' in result:summary=f"处理完成，共{len(result['results'])}项"
        elif isinstance(result,list):summary=f'处理完成，共{len(result)}项'
        else:summary='处理完成'
    finished=now() if status in ('done','error','interrupted') else None
    with state.db:state.db.execute('UPDATE ui_tasks SET status=?,finished_at=?,phase=COALESCE(?,phase),summary=?,error=? WHERE id=?',
                                   (status,finished,phase,summary,str(error)[:1500] if error else None,ident))


def recent(state,*,offset=0,limit=100,query='',status='all'):
    cutoff=(datetime.fromisoformat(now())-timedelta(days=7)).isoformat(timespec='seconds')
    # File audit rows are retained indefinitely; only their default view is seven days.
    tasks=[{'id':r['id'],'kind':'task','status':r['status'],'at':r['finished_at'] or r['started_at'],
            'title':r['title'],'task':dict(r)} for r in state.db.execute('SELECT * FROM ui_tasks WHERE started_at>=? ORDER BY started_at DESC',(cutoff,))]
    moves=[]
    for r in state.db.execute("SELECT id,status,updated_at,json_remove(data,'$.manifest') AS data,json_array_length(data,'$.manifest.files') AS file_count FROM operations WHERE updated_at>=? OR status IN ('prepared','moved','verified','recovery_required')",(cutoff,)):
        moves.append({'id':r['id'],'kind':'operation','status':r['status'],'at':r['updated_at'],'data':json.loads(r['data']),'file_count':r['file_count'] or 0})
    rows=sorted(tasks+moves,key=lambda r:(r['at'],r['id']),reverse=True)
    if query:rows=[r for r in rows if query.casefold() in json.dumps([r.get('title'),r.get('data',{}).get('source'),r.get('data',{}).get('target'),r.get('task',{}).get('summary')],ensure_ascii=False).casefold()]
    if status!='all':
        rows=[r for r in rows if (r['status'] in ('done','committed','already_committed') if status=='committed' else r['status'] not in ('done','committed','already_committed') if status=='errors' else r['status']==status)]
    return {'rows':rows[offset:offset+limit],'total':len(rows),'offset':offset,'limit':limit,'retention_days':7}
