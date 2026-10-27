'use strict';
const $ = (s, root=document) => root.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S = {page:'inbox',boot:null,rows:[],pending:[],resolved:[],includeRoot:false,views:{}};
function view(page=S.page){return S.views[page]||(S.views[page]={selected:new Set(),focus:null,detail:null,filter:'all',query:'',preview:null,scanned:null,operations:[],acting:false,job:null,unread:0,dirty:false,historyOffset:0,historyTotal:0});}
for(const key of ['selected','focus','detail','filter','query','preview','scanned','operations','acting','undoTicket','aiTicket'])Object.defineProperty(S,key,{get:()=>view()[key],set:value=>view()[key]=value});
Object.defineProperty(S,'busy',{get:()=>!!view().job||!!view().loading});
const labels = {resolved_external:'已自行处理',move:'可归档',review:'待确认',conflict:'同名冲突',skip:'保持原位',pending:'待审核',waiting_user:'待确认',waiting_content:'内容不可用',correction_required:'归属建议',committed:'已完成',already_committed:'已完成',failed:'失败',prepared:'准备中',moved:'已移动待验证',verified:'已验证待记账',recovery_required:'需恢复记录',confirmed:'已确认',manual_required:'需人工核对',source_retained:'已归档，原件未完全清理',rejected:'已拒绝'};
const titles = {inbox:['收件箱','从新资料开始，先核对，再整理。'],review:['审核与待办','处理不确定的归属，让整理决定有据可循。'],history:['操作记录','查看每一次移动，以及它的核验结果。'],settings:['规则与设置','管理本机规则、执行范围与后台状态。']};
Object.assign(labels,{done:'已完成',running:'进行中',queued:'排队中',error:'失败',interrupted:'已中断'});
function badge(s){return `<span class="badge ${['move','committed','confirmed','resolved_external'].includes(s)?'green':['review','pending','waiting_user','correction_required'].includes(s)?'amber':['conflict','failed','recovery_required','rejected'].includes(s)?'red':''}">${esc(labels[s]||s)}</span>`;}
function filename(path){return String(path).split('/').pop();}
function icon(path){const n=filename(path);return `<span class="file-icon">${esc(n.includes('.')?n.split('.').pop().slice(0,5).toUpperCase():'目录')}</span>`;}
function note(text,error=false,page=S.page){view(page).notice={text,error};if(S.page===page){const n=$('#notice');n.textContent=text;n.hidden=false;n.classList.toggle('error',error);}}
async function api(path,body){
 const retryable=body===undefined||!!body._request_id;
 for(let attempt=0;;attempt++){
  let r,value;
  try{r=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-Storage-Token':S.boot.token},body:body===undefined?undefined:JSON.stringify(body)});value=await r.json();}
  catch(error){if(error instanceof TypeError&&retryable&&attempt<2){await new Promise(resolve=>setTimeout(resolve,150*(attempt+1)));continue;}throw Error('本地请求 '+path+' 未完成，请检查服务连接。');}
  if(!r.ok)throw Error(value.error||'本地服务请求失败');return value;
 }
}

async function job(action,body={},page=S.page){
 const v=view(page);if(v.job)throw Error('此页面已有任务运行，可以切换到其他页面。');
 v.taskFailed=false;v.job={action,phase:'正在提交本地任务…'};syncTaskUI();
 try{const {job_id}=await api(action,{...body,_page:page,_request_id:crypto.randomUUID()});v.job.id=job_id;while(true){const j=await api('jobs/'+job_id);v.job.phase=(j.state==='queued'?'正在排队，前一项任务完成后继续':j.phase)+(j.total?` · 已完成 ${j.completed||0} / ${j.total} 项`:'');syncTaskUI();if(j.state==='error'){const error=Error(j.error);error.cancelled=j.cancelled;throw error;};if(j.state==='done')return j.result;await new Promise(r=>setTimeout(r,250));}}
 catch(error){v.taskFailed=!error.cancelled;note(error.message,!error.cancelled,page);throw error;}
 finally{v.job=null;if(S.page!==page)v.unread++;view('history').dirty=true;syncTaskUI();}
}
function disableActions(disabled){document.querySelectorAll('button[data-action]').forEach(b=>{if(['close-modal','to-review','ai-page','open-inbox','reveal','copy','history-prev','history-next'].includes(b.dataset.action))return;if(disabled){if(b.dataset.wasDisabled===undefined)b.dataset.wasDisabled=b.disabled?'1':'0';b.disabled=true;}else if(b.dataset.wasDisabled!==undefined){b.disabled=b.dataset.wasDisabled==='1';delete b.dataset.wasDisabled;}});}
function syncTaskUI(){
 for(const button of document.querySelectorAll('nav [data-page]')){const v=view(button.dataset.page);let marker=$('.nav-task',button);if(!marker){marker=document.createElement('span');marker.className='nav-task';button.append(marker);}marker.hidden=!v.job&&!v.unread;marker.textContent=v.job?'':String(v.unread);marker.classList.toggle('running',!!v.job);marker.classList.toggle('task-error',!!v.taskFailed&&!v.job);button.title=v.job?v.job.phase:v.unread?`${v.unread} 项任务已完成，点击查看`:'';marker.setAttribute('aria-label',v.job?'任务运行中':`${v.unread} 项任务已完成`);}
 const active=view().job;$('#job-bar').hidden=!active;if(active)$('#job-bar').textContent=active.phase+' · 可以切换页面，任务会继续运行';disableActions(!!active);
}
async function refreshStatus(){const boot=await api('bootstrap');S.boot=boot;const st=boot.status;$('#connection').textContent=st.paused?'执行已暂停':'本地服务已连接';$('#root-label').textContent=st.root;$('#watch-status').textContent=st.watching?(st.paused?'后台运行 · 执行已暂停':'后台正在运行'):'后台未运行';if(st.watch_error)note('后台已停止：'+st.watch_error,true);if(st.log_export_warning)note('记录导出提示：'+st.log_export_warning,true);}
async function refreshPage(page=S.page){
 const v=view(page),sequence=v.sequence=(v.sequence||0)+1,previousRows=page==='review'?pageRows(page):[];await refreshStatus();
 const pending=await api('pending');if(sequence!==v.sequence)return;S.pending=pending;$('#pending-count').textContent=pending.length||'';
 if(page==='history'){const result=await api('activity?offset='+v.historyOffset+'&query='+encodeURIComponent(v.query)+'&status='+encodeURIComponent(v.filter));if(sequence!==v.sequence)return;v.operations=result.rows;v.historyTotal=result.total;if(v.focus){const row=result.rows.find(r=>r.id===v.focus.record_id);if(row)v.focus={...row,id:row.id_handle,record_id:row.id};}}
 let reviewChanged=false;
 if(page==='review'){const [ops,resolved]=await Promise.all([api('operations'),api('review-history')]);if(sequence!==v.sequence)return;v.operations=ops;S.resolved=resolved;reviewChanged=reconcileReviewFocus(previousRows);}
 if(sequence!==v.sequence)return;v.dirty=false;if(S.page===page){render();if(reviewChanged){$('#detail').scrollTop=0;$('#rows tr.selected')?.scrollIntoView({block:'nearest'});}}
}
function reviewKey(row){return row.review_id?'review:'+row.review_id:row.data?'operation:'+(row.record_id||row.id):'path:'+(row.path||row.source);}
function reconcileReviewFocus(previousRows){
 const v=view('review'),previous=v.focus;if(!previous)return false;
 const rows=pageRows('review'),key=reviewKey(previous);let next=rows.find(r=>reviewKey(r)===key);
 if(!next){const index=previousRows.findIndex(r=>reviewKey(r)===key);for(const old of previousRows.slice(index+1)){next=rows.find(r=>reviewKey(r)===reviewKey(old));if(next)break;}next=next||rows[0];}
 const changed=!next||reviewKey(next)!==key;
 if(changed||next.available!==previous.available||JSON.stringify(next.details)!==JSON.stringify(previous.details))v.detail=null;
 v.focus=next?{...next,id:next.id_handle||next.id,record_id:next.id}:null;
 if(changed){v.preview=null;v.aiTicket=null;v.undoTicket=null;}
 return changed;
}
async function load(page=S.page){
 const loadingView=view(page);loadingView.loading=(loadingView.loading||0)+1;
 try{
 if($('#modal').open)$('#modal').close();S.page=page;const v=view(page);v.unread=0;v.taskFailed=false;
 $('nav .active')?.classList.remove('active');$(`nav [data-page="${page}"]`).classList.add('active');$('#page-title').textContent=titles[page][0];$('#page-description').textContent=titles[page][1];$('#notice').hidden=true;if(v.notice)note(v.notice.text,v.notice.error,page);
 if(S.boot)render();syncTaskUI();await refreshPage(page);
 if(page==='inbox'&&(!v.scanned||v.needsScan)&&!v.job)await scan();
 if(S.page===page&&v.dialog?.pending){v.dialog.pending=false;modal(v.dialog.title,v.dialog.html,page);}
 }finally{loadingView.loading--;if(S.page===page){if($('#rows'))renderRows();syncTaskUI();}}
}
async function scan(options={}){
 const page='inbox',v=view(page);if(v.scanPromise)return v.scanPromise;
 v.scanPromise=(async()=>{v.preview=null;v.selected.clear();const result=await job('scan',{include_root:S.includeRoot,use_ai:options.use_ai===true},page);S.rows=result.rows;v.scanned=result.scanned_at;v.scanStats=result;S.pending=await api('pending');$('#pending-count').textContent=S.pending.length||'';v.needsScan=false;v.focus=null;v.detail=null;if(S.page===page)render();})();
 try{await v.scanPromise;}finally{v.scanPromise=null;}
}
async function settled(page,text,error=false,filesChanged=false){if(filesChanged)view('inbox').needsScan=true;if(text)note(text,error,page);await refreshPage(page);if(filesChanged&&S.page===page&&page==='inbox')await scan();if(S.page==='history'&&page!=='history')await refreshPage('history');}
function toolbar(){return `<div class="toolbar"><button data-action="open-inbox">↗ 打开收件箱</button><button class="primary" data-action="scan">↻ 扫描收件箱</button><label class="scope"><input id="include-root" type="checkbox" ${S.includeRoot?'checked':''}>同时扫描根目录散落文件</label><span class="spacer"></span><button data-action="to-review">审核与待办 ${S.pending.length?`(${S.pending.length})`:''} →</button></div>`;}
function empty(title,text){return `<div class="empty"><div class="empty-icon">▱</div><strong>${esc(title)}</strong><p>${esc(text)}</p></div>`;}
function filters(type){return `<div class="filters"><input id="query" type="text" placeholder="${type==='history'?'搜索最近7天的记录':'查找文件名或路径'}" value="${esc(S.query)}" aria-label="筛选文件"><select id="filter" aria-label="筛选状态">${(type==='inbox'?[['all','全部状态'],['move','可归档'],['review','待确认'],['conflict','同名冲突'],['skip','保持原位']]:type==='review'?[['all','全部待办'],['waiting_user','归属待确认'],['correction_required','审核建议'],['pending','待审核'],['waiting_content','内容不可用'],['errors','操作异常'],['resolved_external','已处理记录']]:[['all','全部状态'],['committed','已完成'],['errors','异常与未完成']]).map(([v,t])=>`<option value="${v}" ${S.filter===v?'selected':''}>${t}</option>`).join('')}</select></div>`;}
function matches(row,page=S.page){if(page==='history')return true;const v=view(page),path=row.source||row.path||row.data?.source||'';const st=row.decision||row.status;return (path+' '+(row.target||row.data?.target||'')).toLowerCase().includes(v.query.toLowerCase())&&(v.filter==='all'||st===v.filter||(v.filter==='errors'&&!['committed','already_committed'].includes(st)));}
function pageRows(page=S.page){const v=view(page);let rows;if(page==='inbox')rows=S.rows;else if(page==='history'||v.filter==='errors')rows=v.operations.filter(r=>page==='history'||r.status!=='committed');else rows=v.filter==='resolved_external'?S.resolved:S.pending;return rows.filter(r=>matches(r,page));}
function render(){const scrolls={rows:$('#rows')?.scrollTop||0,detail:$('#detail')?.scrollTop||0};const c=$('#content');c.classList.toggle('list-page',!['settings','ai'].includes(S.page));if(S.page==='settings'){renderSettings();return;}if(S.page==='inbox'){c.innerHTML=toolbar()+`<div class="panel split"><div class="table-area"><div class="panel-head">待整理资料 <small>${S.rows.length} 项 · ${S.scanned?'扫描于 '+esc(S.scanned.slice(11,19)):'尚未扫描'}</small></div>${filters('inbox')}<div id="rows"></div><div class="table-bottom"><span id="selection-count">已选 ${S.selected.size} 项</span><button class="primary" data-action="preview-selected" ${S.selected.size?'':'disabled'}>预览已选计划 →</button></div></div><aside class="detail" id="detail"></aside></div><p class="hint">完整资料包按目录整体处理。待确认项需单独处理；已整理资料保持原位。文件请通过资源管理器放入收件箱。</p>`;}
 else if(S.page==='review'){c.innerHTML=`<div class="toolbar"><span class="badge amber">离线审核 · API 未接入</span><span class="spacer"></span><label class="scope"><input type="checkbox" id="with-content">导出含文字摘录</label><button data-action="export-review">↓ 导出待审任务</button><button data-action="import-review">↑ 导入审核结果</button></div><div class="panel split"><div class="table-area"><div class="panel-head">需要你的判断 <small>${S.pending.length} 项待办</small></div>${filters('review')}<div id="rows"></div></div><aside class="detail" id="detail"></aside></div><p class="hint">选择一次归属只影响当前资料，不会创建长期规则。离线审核结果只形成建议，移动仍须预览后执行。</p>`;}
 else{c.innerHTML=`<div class="toolbar"><span class="path">最近 7 天 · 关闭软件后保留 · 按时间倒序</span><span class="spacer"></span><button data-action="recover">检查并恢复记录</button></div><div class="panel split"><div class="table-area"><div class="panel-head">任务与移动记录 <small>${view().historyTotal} 条</small></div>${filters('history')}<div id="rows" aria-label="操作记录列表" tabindex="0"></div><div class="table-bottom"><span>${view().historyTotal?view().historyOffset+1:0}–${Math.min(view().historyOffset+100,view().historyTotal)} / ${view().historyTotal} 条</span><div><button data-action="history-prev" ${view().historyOffset?'':'disabled'}>上一页</button> <button data-action="history-next" ${view().historyOffset+100<view().historyTotal?'':'disabled'}>下一页</button></div></div></div><aside class="detail" id="detail"></aside></div><p class="hint">恢复仅核对并补记已经发生的动作。撤销前检查原位置和当前内容，确认后返回原位置。</p>`;}
 renderRows();renderDetail();if($('#rows'))$('#rows').scrollTop=scrolls.rows;if($('#detail'))$('#detail').scrollTop=scrolls.detail;syncTaskUI();}
function renderRows(){const items=pageRows();if(!items.length){$('#rows').innerHTML=empty(S.query||S.filter!=='all'?'没有匹配项目':S.page==='inbox'?'收件箱已清空':S.page==='review'?'暂无待处理事项':'暂无操作记录',S.page==='inbox'?(S.pending.length?`还有 ${S.pending.length} 项需要在“审核与待办”中确认。`:'将新文件放入收件箱，然后扫描即可开始。'):'可以刷新页面查看最新状态。');return;}
 $('#rows').innerHTML=`<div class="table-scroll"><table><thead><tr>${S.page==='inbox'?'<th style="width:32px"><input id="select-all" type="checkbox" aria-label="全选可归档项"></th>':''}<th>文件 / 资料包</th><th>${S.page==='inbox'?'建议位置':S.page==='history'?'运行时间':'状态与原因'}</th><th>状态</th></tr></thead><tbody>${items.map(r=>{const id=r.id_handle||r.id;const task=r.kind==='task';const path=r.source||r.path||r.data?.source||r.title;const status=r.decision||r.status;return `<tr data-row="${esc(id)}" class="${S.focus?.id===id?'selected':''}">${S.page==='inbox'?`<td><input type="checkbox" data-select="${esc(id)}" aria-label="选择 ${esc(filename(path))}" ${S.selected.has(id)?'checked':''} ${status==='move'?'':'disabled'}></td>`:''}<td><div class="file-cell">${task?'<span class="file-icon">任务</span>':icon(path)}<div><strong>${esc(filename(path))}</strong><span class="sub">${esc(S.page==='history'?(task?`任务 · ${titles[r.task.page]?.[0]||r.task.page}`:`批次 ${(r.data.run_id||'').slice(0,8)} · ${r.file_count} 个文件`):r.source_label||path.substring(0,path.lastIndexOf('/')))}</span></div></div></td><td class="path">${esc(S.page==='inbox'?(r.target?r.target.substring(0,r.target.lastIndexOf('/')):r.reason):S.page==='history'?r.at?.replace('T',' ').slice(0,19):reason(r.details?.resolution?.reason||r.details?.reason||r.data?.error))}</td><td>${badge(status)}</td></tr>`;}).join('')}</tbody></table></div>`;
 if(S.page==='inbox'){$('#selection-count').textContent=`已选 ${S.selected.size} 项`;const btn=$('[data-action="preview-selected"]');btn.disabled=S.selected.size===0||S.busy;}}
function reason(text){return ({classification_unresolved:'规则无法确定归属',new_assignment:'归属待审核',content_or_assignment_changed:'内容或归属发生变化',content_changed:'内容发生变化'})[text]||text||'需要人工核对';}
function focusRow(id){const r=S.rows.find(r=>r.id===id)||S.pending.find(r=>r.id===id)||S.resolved.find(r=>r.id===id)||S.operations.find(r=>r.id_handle===id);if(!r)return;S.focus={...r,id,record_id:r.id};S.detail=null;renderRows();renderDetail();}
function renderDetail(){const area=$('#detail');if(!area)return;if(!S.focus){area.innerHTML=S.page==='review'&&!pageRows('review').length?'':'<h3>资料详情</h3>'+empty('选择一项，查看整理依据','当前路径、建议目标和可用操作将在这里展示。');return;}if(S.focus.kind==='task'){renderTaskDetail(area,S.focus);return;}const r=S.focus,path=r.source||r.path||r.data?.source;const op=!!r.data;const d=S.detail;const target=r.target||r.details?.result?.target_parent||r.details?.assignment?.destination_parent||r.data?.target;area.innerHTML=`<h3>${op?'操作详情':'资料详情'}</h3><div class="detail-name">${icon(path)}<strong>${esc(filename(path))}</strong></div><div class="field"><label>${op?'原位置':'当前位置'}</label><div class="value">${esc(r.absolute_path||r.data?.source_absolute||path)}</div></div>${target?`<div class="field"><label>${op?'移动后位置':'建议 / 当前归属'}</label><div class="target-box">${esc(target)}</div></div>`:''}<div class="field"><label>整理依据</label><div class="value">${esc(r.evidence||r.reason||r.data?.evidence||r.data?.error||r.details?.resolution?.reason||r.details?.result?.reason||reason(r.details?.reason))}</div></div>${r.available===false&&r.status!=='resolved_external'?'<p class="hint">历史记录中的原路径当前不存在。若已在资源管理器处理，可点击下方按钮完成此待办；系统不会猜测新位置。</p>':''}${r.details?.resolution?`<p class="hint">处理时间：${esc(r.details.resolution.at.replace('T',' '))}<br>仅关闭待办，保留历史记录；未核验新位置。</p>`:''}${badge(r.decision||r.status)}${r.file_count?` <span class="sub">资料包内 ${r.file_count} 个文件</span>`:''}${d?`<div class="field" style="margin-top:20px"><label>本地文字预览${d.assignment?.manual_locked?' · 人工归属已锁定':''}</label><pre>${esc(d.features.text||'未提取到文字。空文件、目录或不支持的格式可在资源管理器中查看。')}</pre>${(d.features.warnings||[]).map(w=>`<p class="hint">${esc(w)}</p>`).join('')}${d.file_count>1?`<details><summary>包内成员（前80项）</summary><pre>${esc(d.members.join('\n'))}</pre></details>`:''}</div>`:''}<div class="detail-actions"><button data-action="reveal">↗ 显示所在目录</button><button data-action="copy">复制路径</button>${!op&&r.available!==false&&r.status!=='resolved_external'?'<button data-action="detail">读取详情与预览</button><button data-action="choose-target">选择归属位置</button>':''}${r.decision==='review'&&!r.external?'<button data-action="stage-review">移入待确认…</button>':''}${r.review_id&&r.available!==false&&r.status!=='resolved_external'?`<button data-action="keep" ${d?'':'disabled'}>保留当前归属</button>`:''}${r.review_id&&r.available===false&&r.status!=='resolved_external'?'<button class="primary" data-action="resolve-external">已在资源管理器处理</button>':''}${op&&r.status==='committed'&&!['external_copy','external_move'].includes(r.data?.operation_type)?'<button data-action="undo-preview">检查撤销…</button>':''}</div>${op?`<details><summary>核验与技术详情</summary><pre>${esc(JSON.stringify({...r.data,file_count:r.file_count},null,2))}</pre></details>`:''}`;}
function modal(title,html,page=S.page){const v=view(page);v.dialog={title,html,pending:S.page!==page};if(S.page!==page)return;$('#modal-title').textContent=title;$('#modal-body').innerHTML=html;if(!$('#modal').open)$('#modal').showModal();}
function closeModal(page=S.page){view(page).dialog=null;if(S.page===page)$('#modal').close();}
function archiveModeSummary(a){
 if(a.operation_type==='external_move')return (a.reuse_existing?'转移归档：复用已校验副本，本次仅清理原件。':'转移归档：复制并完整校验成功后删除原件。')+(a.cleanup_complete===true?' 原件已清理。':a.cleanup_complete===false?' 原件未完全清理。':'');
 if(a.operation_type==='external_copy')return '复制归档：保留原件。';
 return '库内移动：归档后原位置不再保留。';
}
function executeLabel(p){return p.actions.some(a=>a.operation_type==='external_move')?'确认归档并删除转移项原件（'+p.actions.length+' 项）':'执行这 '+p.actions.length+' 项';}
async function preview(ids,extra={},page=S.page){
 const v=view(page);v.preview=null;const p=await job('preview',{ids,...extra},page);v.preview=p;
 modal(extra.waiting?'移入待确认 · 执行预览':'整理计划 · 执行预览',`<p>本次只处理以下 ${p.actions.length} 项。保留原名与内容。请核对每项归档方式：转移模式会在副本完整校验后删除源文件。</p>${p.actions.map(a=>`<div class="preview-item"><strong>${esc(filename(a.source))}</strong><div class="path">${esc(a.source)}</div><div class="arrow">↓</div><div>${esc(a.target)}</div><p class="hint">${archiveModeSummary(a)}</p><span class="sub">${esc(a.evidence)}</span></div>`).join('')}<p class="hint">预览有效期15分钟。文件、规则或归属变化后需重新预览。</p><div class="modal-actions"><button data-action="close-modal">返回</button><button class="primary" data-action="execute">${executeLabel(p)}</button></div>`,page);
}
async function chooseTarget(page=S.page){
 const v=view(page),focus=v.focus;if(!focus)return;
 if(!v.detail){const detail=await job('detail',{id:focus.id},page);if(v.focus?.id!==focus.id)return;v.detail=detail;if(S.page===page)renderDetail();}
 const stack=[];let chosen=null;
 async function draw(id){
  modal('选择本次资料的归属','<p>正在读取可用目录…</p>',page);
  const data=await api('directories'+(id?'?id='+encodeURIComponent(id):''));chosen=id?{id,path:data.path}:null;
  if(S.page!==page){note('目录已准备好，点击“选择归属位置”继续。',false,page);return;}
  modal('选择本次资料的归属',`<p>仅影响当前资料，选择已有目录后填写判断依据。</p><div class="toolbar" style="margin-top:15px"><button id="dir-back" ${stack.length?'':'disabled'}>← 上一级</button><div class="pick-path">${esc(data.path||'选择资料分类')}</div></div><div class="directory-list">${data.items.map(r=>`<button data-dir="${r.id}"><span>▱ ${esc(r.name)}</span><span>›</span></button>`).join('')||'<p>此目录没有可选的下级目录。</p>'}</div><div style="margin-top:22px"><label class="hint" for="evidence">本次归属依据（必填）</label><textarea id="evidence" placeholder="例如：这是某项目专用的学习资料，随该项目保存。"></textarea></div><div class="modal-actions"><button data-action="close-modal">取消</button><button class="primary" id="use-dir" ${chosen?'':'disabled'}>使用此目录并预览</button></div>`,page);
  $('#dir-back').onclick=()=>draw(stack.pop()).catch(handleError);
  document.querySelectorAll('[data-dir]').forEach(b=>b.onclick=()=>{stack.push(id);draw(b.dataset.dir).catch(handleError);});
  $('#use-dir').onclick=()=>{if(v.job)return;const evidence=$('#evidence').value.trim();if(!evidence){note('请填写本次归属依据。',true,page);return;}preview([focus.id],{target_id:chosen.id,evidence},page).catch(e=>note(e.message,true,page));};
 }
 await draw();
}
function renderSettings(){const b=S.boot,s=b.status,p=b.policy;$('#content').innerHTML=`<div class="settings-grid"><div><div class="panel settings-card"><h2>资料与分类规则</h2><div class="setting-row"><span>资料主目录</span><strong>${esc(s.root)}</strong></div><div class="setting-row"><span>规则版本 / 协议</span><strong>${esc(p.policy_version)} / ${esc(p.schema_version)}</strong></div><div class="setting-row"><span>执行规则 / 登记任务</span><strong>${p.executable_rules.length} / ${(p.tasks||[]).length}</strong></div><div class="setting-row"><span>已索引条目（历史观测）</span><strong>${s.indexed_items}</strong></div><details><summary>查看当前可执行规则</summary><pre>${esc(JSON.stringify(p.executable_rules,null,2))}</pre></details><details><summary>查看任务绑定和保护约束</summary><pre>${esc(JSON.stringify({tasks:p.tasks,constraints:p.constraints},null,2))}</pre></details><p>规则在此只读。修改本地规则后，旧计划失效，后台需重新授权。</p></div><div class="panel settings-card"><h2>管理状态快照</h2><p>导出本地索引、操作状态与配套规则文本。快照不包含原资料正文，也不能代替资料备份。</p><div class="toolbar"><button data-action="snapshot">↓ 导出管理快照</button></div><details><summary>状态保存位置</summary><pre>${esc(s.profile)}</pre></details></div></div><div><div class="panel settings-card"><h2>执行与后台运行</h2><div class="status-banner">${s.paused?'执行已暂停':'执行未暂停'} · ${s.watching?'后台正在运行':'后台未运行'}</div><div class="setting-row"><span>持续授权</span><strong>${b.authorization_current?'当前规则已授权':s.background_authorized?'授权已过期，规则已变化':'尚未授权'}</strong></div><div class="setting-row"><span>授权范围</span><strong>${b.authorization_scope_root?'收件箱及根目录散落文件':'仅收件箱'}</strong></div><div class="toolbar"><button data-action="toggle-pause">${s.paused?'恢复执行':'暂停执行'}</button><button data-action="watch-start" ${b.authorization_current&&!s.watching?'':'disabled'}>启动后台</button><button data-action="watch-stop" ${s.watching?'':'disabled'}>停止后台</button></div><div class="toolbar"><label class="scope"><input type="checkbox" id="authorize-root">授权包含根目录散落文件</label></div><div class="toolbar"><button data-action="authorize">${s.background_authorized?'更新持续授权':'允许持续自动整理'}</button><button data-action="revoke" ${s.background_authorized?'':'disabled'}>撤销授权</button></div><p class="hint">授权后仍须点击“启动后台”。后台每60秒检查一次，处理收件箱内可归档项及待确认分流。关闭浏览器后服务继续运行；关闭启动服务的窗口会停止。停止请求将在当前批次结束后生效。</p><div class="toolbar"><button data-action="shutdown-preview">退出本地服务…</button></div></div><div class="panel settings-card"><h2>AI 审核接口</h2><span class="badge">API 未接入</span><p style="margin-top:12px">第一版使用规则判断、人工归属和离线审核结果。模型接口已保留，尚未启用联网或要求提供密钥。</p></div></div></div>`;}
function handleError(e){note(e.message||String(e),true);}
async function perform(action,page=S.page){
 const v=view(page),focus=v.focus;
 switch(action){
 case 'open-inbox':await api('open',{root:'inbox'});break;
 case 'scan':await scan();break;
 case 'to-review':await load('review');break;
 case 'preview-selected':await preview([...v.selected],{},page);break;
 case 'detail':{const detail=await job('detail',{id:focus.id},page);if(v.focus?.id===focus.id)v.detail=detail;if(S.page===page)renderDetail();break;}
 case 'reveal':await api('open',{id:focus.id});break;
 case 'copy':{const path=v.detail?.absolute_path||focus.absolute_path||S.boot.status.root+'\\'+(focus.path||focus.source||focus.data.target).replaceAll('/','\\');await navigator.clipboard.writeText(path);note('完整路径已复制。',false,page);break;}
 case 'choose-target':await chooseTarget(page);break;
 case 'stage-review':await preview([focus.id],{waiting:true},page);break;
 case 'execute':{
  const p=v.preview;if(!p)throw Error('预览已失效');v.preview=null;closeModal(page);
  const result=await job('execute',{plan_id:p.id},page);const good=['committed','already_committed'];
  const success=result.results.filter(r=>good.includes(r.status)).length,errors=result.results.filter(r=>!good.includes(r.status));
  await settled(page,`本次完成 ${success} / ${result.results.length} 项。`+(errors.length?' 未完成项请查看操作记录：'+errors.map(r=>r.error||r.reason||r.status).join('；'):''),!!errors.length,true);break;
 }
 case 'resolve-external':await job('resolve-external',{id:focus.id},page);await settled(page,'已标记为自行处理并移出待办，可在“已处理记录”中查看。未改动实际文件。');break;
 case 'keep':{const result=await job('keep',{id:focus.id},page);if(result.some(r=>r.status==='rejected'))throw Error(result.map(r=>r.error||'').join('；'));await settled(page,'已保留当前归属。');break;}
 case 'export-review':{const content=$('#with-content').checked;const r=await job('review-export',{with_content:content},page);download(r);await settled(page,'待审任务已导出，审核结果导入后仍需人工决定是否移动。');break;}
 case 'import-review':$('#import-file').click();break;
 case 'recover':{const r=await job('recover',{},page);await settled(page,`已检查 ${r.length} 条未完成记录。`);break;}
 case 'undo-preview':{const r=await job('undo-preview',{id:focus.id},page);v.undoTicket=r.ticket;modal('撤销移动 · 核对结果',`<p>当前内容与记录一致，原位置未被占用。确认后将返回：</p><div class="preview-item">${esc(r.source)}<div class="arrow">↓</div>${esc(r.target)}</div><p class="hint">执行时会再次核对。不覆盖现有文件。</p><div class="modal-actions"><button data-action="close-modal">返回</button><button class="primary" data-action="undo">确认返回原位置</button></div>`,page);break;}
 case 'undo':{const ticket=v.undoTicket;closeModal(page);await job('undo',{ticket},page);await settled(page,'已返回原位置，校验通过。',false,true);break;}
 case 'toggle-pause':await job('pause',{paused:!S.boot.status.paused},page);await settled(page);break;
 case 'authorize':{const include_root=$('#authorize-root').checked;await job('authorize',{include_root},page);await settled(page,'持续授权已保存。点击“启动后台”后才开始轮询。');break;}
 case 'revoke':await job('revoke',{},page);await settled(page,'持续授权已撤销，后台将停止。');break;
 case 'watch-start':await job('watch-start',{},page);await settled(page);break;
 case 'watch-stop':await job('watch-stop',{},page);await settled(page,'已请求停止后台，当前批次结束后生效。');break;
 case 'snapshot':{const r=await job('snapshot',{},page);download(r);note('管理状态快照已生成，不包含原资料。',false,page);break;}
 case 'history-prev':v.historyOffset=Math.max(0,v.historyOffset-100);await refreshPage(page);break;
 case 'history-next':v.historyOffset+=100;await refreshPage(page);break;
 case 'shutdown-preview':modal('退出本地服务','<p>界面将断开连接，后台轮询停止。已进入执行器的批次会完成后退出。</p><div class="modal-actions"><button data-action="close-modal">返回</button><button class="primary" data-action="shutdown">确认退出服务</button></div>',page);break;
 case 'shutdown':clearInterval(statusTimer);await api('shutdown',{});closeModal(page);$('#connection').textContent='本地服务已退出';note('已请求退出本地服务。');document.querySelectorAll('button').forEach(b=>b.disabled=true);break;
 case 'close-modal':closeModal(page);break;
 }
}
function download(r){const a=document.createElement('a');a.href=r.download;a.download=r.filename;document.body.append(a);a.click();a.remove();}

document.addEventListener('click',e=>{
 const button=e.target.closest('button[data-action]');
 if(button){const page=S.page,v=view(page),action=button.dataset.action;
  if(action==='close-modal'){closeModal(page);return;}
  if(button.disabled||v.acting||v.job)return;v.acting=true;
  perform(action,page).catch(error=>note(error.message||String(error),!error.cancelled,page)).finally(()=>{v.acting=false;syncTaskUI();});return;
 }
 const row=e.target.closest('[data-row]');if(row&&!S.busy&&!S.acting&&!e.target.matches('input'))focusRow(row.dataset.row);
});
let historySearchTimer;
document.addEventListener('change',e=>{
 if(e.target.matches('[data-select]')){S.preview=null;e.target.checked?S.selected.add(e.target.dataset.select):S.selected.delete(e.target.dataset.select);renderRows();}
 if(e.target.id==='select-all'){S.preview=null;S.rows.filter(r=>r.decision==='move'&&matches(r)).forEach(r=>e.target.checked?S.selected.add(r.id):S.selected.delete(r.id));renderRows();}
 if(e.target.id==='filter'){S.filter=e.target.value;S.focus=null;S.detail=null;if(S.page==='history'){view().historyOffset=0;refreshPage().catch(handleError);}else{renderRows();renderDetail();}}
 if(e.target.id==='include-root'){S.includeRoot=e.target.checked;S.preview=null;S.selected.clear();S.rows=[];S.scanned=null;S.focus=null;S.detail=null;render();note('扫描范围已变更，请重新扫描。');}
});
document.addEventListener('input',e=>{if(e.target.id==='query'){S.query=e.target.value;if(S.page==='history'){view().historyOffset=0;clearTimeout(historySearchTimer);historySearchTimer=setTimeout(()=>refreshPage('history').catch(handleError),300);}else renderRows();}});
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>load(b.dataset.page).catch(error=>note(error.message,true,b.dataset.page)));
$('#open-library').onclick=()=>api('open',{root:'library'}).catch(handleError);
$('#refresh').onclick=()=>{const page=S.page;if(page==='inbox'&&!S.busy)scan().catch(handleError);else refreshPage(page).catch(handleError);};
$('#modal-close').onclick=()=>closeModal();
$('#modal').addEventListener('cancel',()=>view().dialog=null);
$('#import-file').onchange=async e=>{
 const page='review',v=view(page);try{const f=e.target.files[0];if(!f)return;if(v.job)throw Error('审核页面已有任务运行');if(f.size>4*1024*1024)throw Error('审核结果超过4MiB');const payload=JSON.parse(await f.text());const result=await job('review-import',{payload},page);const rejected=result.filter(r=>r.status==='rejected');await settled(page,`已读取 ${result.length} 项审核结果，拒绝 ${rejected.length} 项。`+(rejected.length?rejected.map(r=>r.error).join('；'):''),!!rejected.length);}catch(error){note(error.message,true,page);}finally{e.target.value='';}
};
load().catch(handleError);
const statusTimer=setInterval(()=>{refreshStatus().then(()=>{if(S.page==='history'&&!S.busy&&!$('#modal').open)refreshPage('history').catch(handleError);}).catch(()=>{$('#connection').textContent='本地服务已断开';});},8000);

function renderTaskDetail(area,r){
 const t=r.task;
 area.innerHTML=`<h3>任务详情</h3><div class="detail-name"><span class="file-icon">任务</span><strong>${esc(r.title)}</strong></div>${badge(r.status)}<div class="field"><label>来源页面</label><div class="value">${esc(titles[t.page]?.[0]||t.page)}</div></div><div class="field"><label>开始时间</label><div class="value">${esc(t.started_at.replace('T',' '))}</div></div><div class="field"><label>完成时间</label><div class="value">${esc(t.finished_at?.replace('T',' ')||'尚未完成')}</div></div><div class="field"><label>处理结果</label><div class="value">${esc(t.error||t.summary||t.phase)}</div></div><p class="hint">任务记录保留七天。文件移动的审计与恢复记录独立保留，不受七天清理影响。</p>`;
}
