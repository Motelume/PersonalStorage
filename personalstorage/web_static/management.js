'use strict';
titles.inboxes=['收件箱管理','按需添加下载、微信或其他来源目录，统一扫描与归档。'];
const managementRefresh=refreshPage;
refreshPage=async function(page=S.page){
 if(page==='inboxes'){
  await refreshStatus();const data=await api('inboxes');view(page).sources=data;
  if(S.page===page)render();return;
 }
 return managementRefresh(page);
};
function importModeOptions(value='copy'){return `<option value="copy" ${value==='copy'?'selected':''}>复制归档，保留原件</option><option value="move" ${value==='move'?'selected':''}>转移归档，成功后删除原件</option>`;}
function renderInboxes(){
 const data=view('inboxes').sources;
 $('#content').classList.remove('list-page');
 if(!data){$('#content').innerHTML=empty('正在读取收件箱配置','');return;}
 $('#content').innerHTML=`<div class="settings-grid"><div><div class="panel settings-card"><h2>已登记的收件箱</h2><p>只扫描已启用目录的直接内容，子文件夹作为完整资料包保留。不会默认读取整个下载区或微信聊天目录。</p>${data.sources.map(s=>`<div class="inbox-source"><div><strong>${esc(s.label)}</strong> <span class="badge ${s.enabled?'green':''}">${s.enabled?'已启用':'已停用'}</span><span class="sub">${esc(s.path)}</span>${!s.available?`<p class="hint">${esc(s.error||'目录不可用')}</p>`:''}</div><div class="toolbar"><button data-source-open="${esc(s.id)}" ${s.available?'':'disabled'}>打开目录</button><button data-source-toggle="${esc(s.id)}" data-enabled="${s.enabled?'false':'true'}">${s.enabled?'停用':'启用'}</button>${!s.internal?`<label>归档方式 <select data-source-mode="${esc(s.id)}" aria-label="${esc(s.label)}的归档方式">${importModeOptions(s.import_mode)}</select></label><button data-source-save="${esc(s.id)}">保存方式</button><button class="danger" data-source-remove="${esc(s.id)}">移除来源</button>`:''}</div></div>`).join('')}</div></div><div><div class="panel settings-card"><h2>添加外部收件箱</h2><div class="field"><label for="inbox-label">显示名称</label><input id="inbox-label" class="full-width" type="text" maxlength="80" placeholder="例如：系统下载、微信接收文件"></div><div class="field"><label for="inbox-path">文件夹绝对路径</label><input id="inbox-path" class="full-width" type="text" placeholder="粘贴资源管理器地址栏中的完整路径"></div><div class="field"><label for="inbox-mode">归档方式</label><select id="inbox-mode" class="full-width">${importModeOptions()}</select></div><div class="toolbar">${S.boot.ui_mode==='desktop'?'<button id="pick-inbox">选择文件夹…</button>':''}${data.suggested_downloads?'<button id="fill-downloads">填写下载目录</button>':''}<button class="primary" data-action="inbox-add">添加收件箱</button></div><p class="hint">微信目录可在微信设置中找到后粘贴；月度目录可单独添加。来源之间不得嵌套，也不能包含程序、资料库或系统目录。</p></div><div class="panel settings-card"><h2>两种归档方式</h2><p>复制归档：完整校验副本后保留原件，后续跳过未变化的已导入项。</p><p>转移归档：先复制并完整校验，再逐个核对、删除原件；仅删除此次预览中的文件，最后移除空目录。遇到占用、变化或中断，保留副本并提示核对，可在操作记录中重试清理。</p><p>每个外部收件箱分别保存归档方式。更改设置不会立即处理文件；执行前会再次显示方式。库内收件箱继续按原有移动流程处理。</p><p class="hint">后续扫描跳过未变化且库内副本仍存在的已导入项。同名冲突保持原位，不覆盖或自动改名。停用来源不删除文件。</p></div></div></div>`;
 $('#pick-inbox')?.addEventListener('click',async()=>{try{const picked=await api('inbox-pick',{});if(S.page==='inboxes'&&picked.path)$('#inbox-path').value=picked.path;}catch(error){handleError(error);}});
 $('#fill-downloads')?.addEventListener('click',()=>{$('#inbox-path').value=data.suggested_downloads;if(!$('#inbox-label').value)$('#inbox-label').value='系统下载';});
 document.querySelectorAll('[data-source-open]').forEach(b=>b.onclick=()=>api('open',b.dataset.sourceOpen==='internal'?{root:'inbox'}:{inbox_id:b.dataset.sourceOpen}).catch(handleError));
 document.querySelectorAll('[data-source-remove]').forEach(b=>b.onclick=()=>{view('inboxes').removingSourceId=b.dataset.sourceRemove;modal('移除收件箱来源','<p>仅停止扫描此来源并关闭该来源的未完成待办。原文件、已归档副本与历史记录都保留。</p><div class="modal-actions"><button data-action="close-modal">返回</button><button class="danger" data-action="inbox-remove">确认移除来源</button></div>','inboxes');});
 document.querySelectorAll('[data-source-save]').forEach(b=>b.onclick=async()=>{try{if(view('inboxes').job)return;const import_mode=document.querySelector(`[data-source-mode="${b.dataset.sourceSave}"]`).value;await job('inbox-mode',{id:b.dataset.sourceSave,import_mode},'inboxes');view('inbox').needsScan=true;await settled('inboxes','归档方式已保存，旧预览已失效；文件尚未处理。');}catch(error){note(error.message,true,'inboxes');}});
 document.querySelectorAll('[data-source-toggle]').forEach(b=>b.onclick=async()=>{try{if(view('inboxes').job)return;await job('inbox-toggle',{id:b.dataset.sourceToggle,enabled:b.dataset.enabled==='true'},'inboxes');view('inbox').needsScan=true;await settled('inboxes','收件箱配置已更新，没有移动或删除文件。');}catch(error){note(error.message,true,'inboxes');}});
}
function auditPanel(){
 const r=S.boot.library_audit,v=view('ai');
 return `<div class="panel settings-card"><h2>全库位置复核</h2><p>仅在点击按钮并确认后启动。检查库内独立文件和完整工程包；已通过且内容、位置与规则未变化的资料不再发送给AI。</p><div class="toolbar"><button data-action="audit-preview" ${S.boot.model_enabled?'':'disabled'}>全库位置复核…</button>${v.auditRunning?'<button id="stop-audit" class="danger">停止后续批次</button>':''}</div><p class="hint">复核库内资料及临时、硬盘待迁资料；管理目录与离线索引除外，暂存工作流会单独判断。这里只提出建议，不自动重排文件。支持分批继续，启动与后台轮询不会触发全库复核。</p>${r?`<div class="audit-summary"><span>最近状态：${esc(({completed:'已完成',running:'运行中',stopped:'已停止',batch_limit:'达到本次上限，可继续',daily_limit:'达到每日上限',error:'需检查错误',partial:'部分完成，可继续'})[r.status]||r.status)}</span><span>位置合理 ${r.approved} · 建议调整 ${r.suggestions} · 待补充 ${r.needs_input}</span><span>复用结果 ${r.cache_hits} · 本次AI ${r.ai_items}项 / ${r.batches}批 · 剩余 ${r.remaining}</span>${r.last_error?`<p class="hint">${esc(r.last_error)}</p>`:''}</div>`:''}</div>`;
}
const managementRender=render;
render=function(){
 if(S.page==='inboxes'){renderInboxes();syncTaskUI();return;}
 managementRender();
 if(S.page==='ai'){
  $('#content').insertAdjacentHTML('afterbegin',auditPanel());
  $('#stop-audit')?.addEventListener('click',()=>api('audit-stop',{}).then(()=>note('已请求停止，当前AI批次完成后不再发送后续批次。')).catch(handleError));
 }
 if(S.page==='inbox'){
  $('.toolbar')?.insertAdjacentHTML('beforeend','<button data-action="manage-inboxes">收件箱管理</button>');
  const button=$('[data-action="scan"]');if(button)button.textContent=S.boot.model_enabled?'↻ 扫描并智能分类':'↻ 规则扫描';
  $('#content').insertAdjacentHTML('beforeend','<p class="hint">智能扫描先执行规则，再把未确定项和必要摘录交给DeepSeek；打开页面和后台轮询只作本地规则扫描。</p>');const stats=view('inbox').scanStats?.classification;
  if(stats)$('#content').insertAdjacentHTML('beforeend',`<p class="hint">规则确定 ${stats.rule_classified} 项 · AI确定 ${stats.ai_classified} 项 · 待补充 ${stats.needs_input} 项${stats.unprocessed?' · 尚未处理 '+stats.unprocessed+' 项':''}${stats.notice?' · '+esc(stats.notice):''}。确定项可勾选后统一预览归档。</p>`);
  const failures=view('inbox').scanStats?.source_errors||[];
  if(failures.length)$('#content').insertAdjacentHTML('beforeend',`<p class="hint">部分来源未扫描：${failures.map(s=>esc(s.label)+'：'+esc(s.error)).join('；')}</p>`);
 }
 syncTaskUI();
};
const managementDetail=renderDetail;
renderDetail=function(){
 managementDetail();const r=S.focus;
 if(r?.data?.operation_type?.startsWith('external_')){const area=$('#detail .detail-actions');area?.insertAdjacentHTML('beforebegin',`<div class="field"><label>归档方式与原件状态</label><p>${archiveModeSummary(r.data)}</p>${r.status==='source_retained'?`<p class="hint">原件尚未完全清理：${esc(r.data.error||'请核对后重试')}。副本已经校验并保留。</p>`:''}</div>`);if(r.status==='source_retained')area?.insertAdjacentHTML('beforeend','<button data-action="cleanup-preview">重新检查并清理原件…</button>');}
if(!r||r.kind==='task'||r.data||r.available===false||r.status==='resolved_external')return;
 const actionArea=$('#detail .detail-actions');if(!actionArea)return;
 const path=r.path||r.source;const notes=view().notes||(view().notes={});
 actionArea.insertAdjacentHTML('beforebegin',`<div class="field purpose-field"><label for="file-purpose">补充这份资料的性质或用途</label><textarea id="file-purpose" maxlength="2000" placeholder="例如：这是某项目的专用学习材料；这是他人的保研经验分享。">${esc(notes[path]??r.user_context??'')}</textarea>${r.details?.assignment?.manual_locked?'<label class="scope"><input id="override-locked" type="checkbox">本次说明用于重新判断此前人工归属</label>':''}<button class="primary" data-action="ai-refine">补充说明并让AI分类</button><p class="hint">将结合文件内容、补充说明和现有目录判断，不必逐层选择文件夹。归属确定后仍须确认归档计划。</p></div>`);
 $('#file-purpose').addEventListener('input',e=>notes[path]=e.target.value);
};
const managementPerform=perform;
perform=async function(action,page=S.page){
 const v=view(page),focus=v.focus;
 if(action==='cleanup-preview'){const r=await job('cleanup-preview',{id:focus.id},page);v.cleanupTicket=r.ticket;modal('清理已归档原件 · 确认',`<p>已重新校验库内副本。确认后将删除以下源位置中仍与归档记录一致的 ${r.file_count} 个文件，并移除空目录。</p><div class="preview-item">原位置：${esc(r.source_absolute)}<br>已验证副本：${esc(r.target)}</div><p class="hint">源或副本发生变化时停止清理。不会重新复制或覆盖副本。</p><div class="modal-actions"><button data-action="close-modal">返回</button><button class="danger" data-action="cleanup-execute">确认清理原件</button></div>`,page);return;}
 if(action==='cleanup-execute'){const ticket=v.cleanupTicket;v.cleanupTicket=null;closeModal(page);const r=await job('cleanup-execute',{ticket},page);await settled(page,r.status==='committed'?'原件已清理，已校验的归档副本保留。':'副本保留，原件未完全清理：'+r.error,r.status!=='committed');return;}
 if(action==='manage-inboxes'){await load('inboxes');return;}
 if(action==='inbox-remove'){
  const id=v.removingSourceId;closeModal(page);await job('inbox-remove',{id},page);view('inbox').needsScan=true;
  await settled(page,'来源已移除，文件与归档副本均保留；该来源未完成待办已停止追踪。');return;
 }
 if(action==='inbox-add'){
  const path=$('#inbox-path').value.trim(),label=$('#inbox-label').value.trim(),import_mode=$('#inbox-mode').value;
  await job('inbox-add',{path,label,import_mode},page);view('inbox').needsScan=true;await settled(page,'收件箱已添加。下次扫描会包含此来源，文件不会立即移动。');return;
 }
 if(action==='scan'){await scan({use_ai:S.boot.model_enabled});return;}
 if(action==='ai-refine'){
  const text=$('#file-purpose').value.trim();if(!text)throw Error('请先补充这份资料的性质、用途或所属任务。');
  v.refiningPath=focus.path||focus.source;await prepareAI([focus.id],page,{[v.refiningPath]:text},{overrideLocked:$('#override-locked')?.checked===true});return;
 }
 if(action==='ai-run'&&v.refiningPath){
  const source=v.refiningPath;v.refiningPath=null;const ticket=v.aiTicket;v.aiTicket=null;closeModal(page);
  const result=await job('ai-run',{ticket},page);await refreshPage(page);
  const pending=await api('pending');S.pending=pending;
  const row=pending.find(item=>item.path===source);
  if(row?.details?.result?.decision==='suggest_reclassify'){
   v.focus=row;const plan=await job('ai-adopt',{id:row.id},page);v.preview=plan;
   modal('AI已结合补充信息确定归属 · 归档预览',`<p>请核对AI建议的实际目标。请逐项核对归档方式；转移模式会在副本校验后删除原件。</p>${plan.actions.map(a=>`<div class="preview-item"><strong>${esc(filename(a.source))}</strong><div class="path">${esc(a.source)}</div><div class="arrow">↓</div>${esc(a.target)}<p class="hint">${archiveModeSummary(a)}</p><p class="hint">${esc(a.evidence)}</p></div>`).join('')}<div class="modal-actions"><button data-action="close-modal">返回</button><button class="primary" data-action="execute">${executeLabel(plan)}</button></div>`,page);
  }else if(result.results.every(item=>item.status==='confirmed')){await settled(page,'AI已结合补充说明确认当前位置合理，此项已完成。');}
  else{await settled(page,'AI仍需更多信息，请查看判断依据并继续补充说明。');}
  return;
 }
 if(action==='audit-preview'){
  modal('全库位置复核 · 确认范围',`<p>将只读检查库内资料的当前归属。会发送必要内容给DeepSeek，但不移动文件。已通过且未变化的资料跳过API；其余按每日限制和本次上限分批处理。</p><div class="field"><label for="audit-batches">本次最多AI批次（1–1000，每批最多 ${S.boot.ai.settings.max_items} 项）</label><input id="audit-batches" type="number" min="1" max="1000" value="20"></div><p class="hint">完整工程包整体复核，内容证据不足时保留待确认；管理目录和离线索引除外，暂存工作流单独判断。达到上限后可再次点击继续。</p><div class="modal-actions"><button data-action="close-modal">返回</button><button class="primary" data-action="audit-start">确认开始全库复核</button></div>`,page);return;
 }
 if(action==='audit-start'){
  const max_batches=Number($('#audit-batches').value);closeModal(page);v.auditRunning=true;if(S.page===page)render();
  try{const r=await job('library-audit',{confirmed:true,max_batches},page);await settled(page,`本次复核：位置合理 ${r.approved} 项、建议调整 ${r.suggestions} 项、待补充 ${r.needs_input} 项；复用结果 ${r.cache_hits} 项。文件未移动。`);}
  finally{v.auditRunning=false;if(S.page===page)render();}return;
 }
 return managementPerform(action,page);
};
