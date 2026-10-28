'use strict';
function wfRows(page=S.page){return page==='inbox'?S.rows:S.pending;}
function wfPath(r){return r.path||r.source;}
function wfChosen(page=S.page){const paths=view(page).batchPaths||new Set();return wfRows(page).filter(r=>paths.has(wfPath(r))&&r.available!==false);}
const wfBaseRows=renderRows;
renderRows=function(){
 wfBaseRows();if(!['inbox','review'].includes(S.page)||['errors','resolved_external'].includes(S.filter))return;
 const v=view(),paths=v.batchPaths||(v.batchPaths=new Set()),available=new Set(wfRows().filter(r=>r.available!==false).map(wfPath));
 for(const p of paths)if(!available.has(p))paths.delete(p);
 for(const row of $$('#rows [data-row]')){const r=wfRows().find(r=>r.id===row.dataset.row);if(!r||r.available===false)continue;row.insertAdjacentHTML('afterbegin',`<td><input type="checkbox" data-wf-select="${esc(wfPath(r))}" aria-label="批量选择 ${esc(filename(wfPath(r)))}" ${paths.has(wfPath(r))?'checked':''}></td>`);}
 $('#rows thead tr')?.insertAdjacentHTML('afterbegin','<th><input type="checkbox" id="wf-all" aria-label="全选当前筛选资料"></th>');
 const visible=pageRows().filter(r=>r.available!==false);if($('#wf-all')){$('#wf-all').checked=visible.length>0&&visible.every(r=>paths.has(wfPath(r)));$('#wf-all').indeterminate=visible.some(r=>paths.has(wfPath(r)))&&!$('#wf-all').checked;}
 $('#wf-toolbar')?.remove();$('.table-area .filters')?.insertAdjacentHTML('beforebegin',`<div class="toolbar" id="wf-toolbar"><span>批量已选 ${paths.size} 项</span><button data-action="wf-manual" ${paths.size?'':'disabled'}>批量指定归属</button><button data-action="wf-ai" ${paths.size?'':'disabled'}>批量补充说明并交给AI</button><button data-action="wf-adopt" ${paths.size?'':'disabled'}>预览所选AI归属</button><button data-action="wf-clear">清除选择</button><button data-action="wf-results">最近批量结果</button><button data-action="wf-history">改名记录</button></div>`);
};
document.addEventListener('change',e=>{
 if(e.target.dataset.wfSelect!==undefined){const v=view();v.batchPaths||=new Set();e.target.checked?v.batchPaths.add(e.target.dataset.wfSelect):v.batchPaths.delete(e.target.dataset.wfSelect);renderRows();}
 if(e.target.id==='wf-all'){const v=view();v.batchPaths||=new Set();for(const r of pageRows().filter(r=>r.available!==false)){if(e.target.checked&&v.batchPaths.size<100)v.batchPaths.add(wfPath(r));else if(!e.target.checked)v.batchPaths.delete(wfPath(r));}renderRows();}
});
const wfBaseDetail=renderDetail;
renderDetail=function(){wfBaseDetail();const r=S.focus;if(!r||r.data||r.available===false||r.status==='resolved_external'||!['inbox','review'].includes(S.page))return;$('#detail .detail-actions')?.insertAdjacentHTML('beforeend','<button data-action="wf-rename">根据内容建议文件名</button>');};
function wfPlans(result,page){
 const v=view(page);v.batchTicket=result.ticket;
 modal('批量归档 · 逐项核对',`<p>仅处理本次选中的资料；取消勾选可排除单项。没有移动任何文件。</p>${result.plans.map(p=>{const a=p.actions[0];return `<div class="preview-item"><label><input type="checkbox" data-wf-plan="${p.id}" checked> ${esc(a.source)}</label><div>↓ ${esc(a.target)}</div><p>${esc(archiveModeSummary(a))}</p><p class="hint">${esc(a.evidence)}</p></div>`;}).join('')}${result.errors.map(r=>`<p class="hint">未生成计划：${esc(r.source)} · ${esc(r.error)}</p>`).join('')}<div class="modal-actions"><button data-action="close-modal">返回</button><button data-action="wf-execute" ${result.plans.length?'':'disabled'}>确认执行勾选计划</button></div>`,page);
}
document.addEventListener('change',e=>{if(e.target.id==='wf-title-choice')$('#wf-name').value=e.target.value;});
function wfRenameDialog(result,page){
 const v=view(page);v.renameDraft={...v.renameDraft,...result};const d=v.renameDraft;
 modal('内容辅助命名 · 建议',`<p>原名：${esc(filename(d.source))}</p><label>建议文件名（保留扩展名）<input id="wf-name" class="full-width" value="${esc(d.suggested||d.original)}"></label>${d.candidates?.length?`<label>正文标题候选<select id="wf-title-choice">${d.candidates.map(n=>`<option>${esc(n)}</option>`).join('')}</select></label>`:''}<label>改名依据<textarea id="wf-reason">${esc(d.reason)}</textarea></label><p>现有名称明确时保留原名；证据不足可以取消。确认前不会修改文件。</p><label>给AI补充性质或用途<textarea id="wf-rename-note" maxlength="2000"></textarea></label><div class="modal-actions"><button data-action="close-modal">保留原名</button><button data-action="wf-rename-ai" ${d.eligible?'':'disabled'}>预览发送给AI</button><button data-action="wf-rename-preview" ${d.eligible?'':'disabled'}>检查改名预览</button></div>`,page);
}
const wfBasePerform=perform;
perform=async function(action,page=S.page){
 const v=view(page);
 if(action==='wf-results'){wfResultDialog(await job('wb-batch-results',{},page),page);return;}
 if(action==='wf-retry'){const paths=(v.batchResult?.results||[]).filter(r=>!['committed','already_committed','skipped'].includes(r.status)).map(r=>r.source);closeModal(page);if(page==='inbox')await scan();else await refreshPage(page);v.batchPaths=new Set(paths.filter(p=>wfRows(page).some(r=>wfPath(r)===p)));renderRows();note('已重新选择仍可用的失败项，请重新核对目标并生成预览。',false,page);return;}
 if(action==='wf-clear'){v.batchPaths=new Set();if(S.page===page)renderRows();return;}
 if(action==='wf-manual'){const rows=wfChosen(page);if(!rows.length)throw Error('请选择文件');await chooseTarget(page,rows.map(r=>r.id));return;}
 if(action==='wf-adopt'){wfPlans(await job('wb-batch-adopt',{ids:wfChosen(page).map(r=>r.id)},page),page);return;}
 if(action==='wf-execute'){
  const ids=$$('[data-wf-plan]:checked').map(r=>r.dataset.wfPlan);if(!ids.length)throw Error('请选择要执行的计划');const ticket=v.batchTicket;v.batchTicket=null;closeModal(page);
  const result=await job('wb-batch-execute',{ticket,plan_ids:ids},page);v.batchPaths=new Set(result.results.filter(r=>!['committed','already_committed'].includes(r.status)).map(r=>r.source));await settled(page,'本批处理完成，请查看逐项结果。',false,true);
  wfResultDialog(result,page);return;
 }
 if(action==='wf-ai'){
  const rows=wfChosen(page);if(!rows.length)throw Error('请选择文件');v.batchObjects=rows.map(r=>({id:r.id,path:wfPath(r),locked:!!r.details?.assignment?.manual_locked}));
  modal('批量指导AI分类',`<label>本批共同背景或性质（必填）<textarea id="wf-common" maxlength="1500"></textarea></label><p>每份资料仍独立判断，结果可以分到不同目录。既有人工锁定继续受到保护。</p>${v.batchObjects.map((r,i)=>`<div class="field"><label>${esc(r.path)} · 单项补充（可选）</label><textarea data-wf-note="${i}" maxlength="500"></textarea>${r.locked?`<label><input type="checkbox" data-wf-override="${i}">明确要求依据本次说明重新判断这一个人工锁定归属</label>`:''}</div>`).join('')}<div class="modal-actions"><button data-action="close-modal">取消</button><button data-action="wf-ai-preview">预览本次发送范围</button></div>`,page);return;
 }
 if(action==='wf-ai-preview'){
  const rows=v.batchObjects,notes={};for(const r of $$('[data-wf-note]'))notes[rows[Number(r.dataset.wfNote)].id]=r.value;
  const override_ids=$$('[data-wf-override]:checked').map(r=>rows[Number(r.dataset.wfOverride)].id);const result=await job('wb-batch-ai-preview',{ids:rows.map(r=>r.id),common:$('#wf-common').value,notes,override_ids},page);v.batchAiTicket=result.ticket;
  modal('批量AI · 发送确认',`<p>${result.documents.length} 项 / ${result.batches} 批 · ${esc(result.model)} · 今日剩余 ${result.daily_remaining} 批。仅发送以下名称、上下文及已授权的必要摘录。</p>${result.documents.map(r=>`<details><summary>${esc(r.source)}</summary><p>${esc(r.user_context)}</p><pre>${esc(r.text||'未授权或未提取正文')}</pre></details>`).join('')}<div class="modal-actions"><button data-action="close-modal">取消</button><button data-action="wf-ai-run">确认发送选中资料</button></div>`,page);return;
 }
 if(action==='wf-ai-run'){
  const ticket=v.batchAiTicket;v.batchAiTicket=null;closeModal(page);const result=await job('wb-batch-ai-run',{ticket},page);await settled(page,'AI批量判断已完成，文件尚未移动；可在待办中核对后选择“预览所选AI归属”。');
  modal('批量AI结果',result.batches.map((r,i)=>`<div class="preview-item">第 ${i+1} 批：${r.status==='done'?'已返回':'未完成'}${r.error?`<p>${esc(r.error)}</p>`:''}${(r.results||[]).map(x=>`<p>${esc(x.source)} · ${esc(labels[x.status]||x.status)} ${esc(x.error||'')}</p>`).join('')}</div>`).join('')+'<button data-action="close-modal">关闭</button>',page);return;
 }
 if(action==='wf-rename'){
  const obj=v.focus;v.renameDraft={id:obj.id,source:wfPath(obj)};wfRenameDialog(await job('wb-rename-suggest',{id:obj.id},page),page);return;
 }
 if(action==='wf-rename-ai'){
  const result=await job('wb-rename-ai-preview',{id:v.renameDraft.id,note:$('#wf-rename-note').value},page);v.renameAiTicket=result.ticket;
  modal('改名建议 · AI发送确认',`<p>模型 ${esc(result.model)}。发送以下数据；AI只给名称建议。</p><pre>${esc(JSON.stringify(result.request,null,2))}</pre><div class="modal-actions"><button data-action="close-modal">取消</button><button data-action="wf-rename-ai-run">确认发送并生成建议</button></div>`,page);return;
 }
 if(action==='wf-rename-ai-run'){const ticket=v.renameAiTicket;v.renameAiTicket=null;closeModal(page);wfRenameDialog(await job('wb-rename-ai-run',{ticket},page),page);return;}
 if(action==='wf-rename-preview'){
  const result=await job('wb-rename-preview',{id:v.renameDraft.id,name:$('#wf-name').value,reason:$('#wf-reason').value},page);v.renameTicket=result.ticket;
  modal('确认文件改名',`<div class="preview-item">${esc(result.source)}<div>↓</div>${esc(result.target)}</div><p>仅修改文件名，保留内容；同名冲突或文件变化时停止。需要时可在改名记录中检查撤销。</p><div class="modal-actions"><button data-action="close-modal">取消</button><button data-action="wf-rename-execute">确认改名</button></div>`,page);return;
 }
 if(action==='wf-rename-execute'){
  const ticket=v.renameTicket;v.renameTicket=null;closeModal(page);await job('wb-rename-execute',{ticket},page);v.focus=null;v.detail=null;await settled(page,'文件名已更新，内容保留。',false,true);return;
 }
 if(action==='wf-history'){
  const data=await job('wb-rename-history',{},page);modal('文件改名记录',data.rows.map(r=>`<div class="preview-item">${esc(r.source)} → ${esc(r.target)}<p>${esc(r.completed_at)}</p><button data-action="wf-undo" data-rename-id="${r.id}">检查撤销</button></div>`).join('')||'<p>尚无改名记录。</p>',page);return;
 }
 if(action==='wf-undo'){
  const id=document.activeElement?.dataset.renameId;if(!id)throw Error('请选择改名记录');const r=await job('wb-rename-undo-preview',{operation_id:id},page);v.renameTicket=r.ticket;
  modal('撤销改名 · 核对',`<p>${esc(r.source)} → ${esc(r.target)}</p><p>原名未被占用且内容与记录一致时才执行。</p><button data-action="wf-rename-execute">确认改名</button><button data-action="close-modal">取消</button>`,page);return;
 }
 return wfBasePerform(action,page);
};

function wfResultRows(result,filter='all'){return result.results.filter(r=>filter==='all'||(filter==='done'?['committed','already_committed'].includes(r.status):filter==='skipped'?r.status==='skipped':!['committed','already_committed','skipped'].includes(r.status))).map(r=>`<div class="preview-item">${esc(r.source)} · ${esc(labels[r.status]||r.status)}<p>${esc(r.error||'')}</p></div>`).join('')||'<p>没有对应项目。</p>';}
function wfResultDialog(result,page){view(page).batchResult=result;modal('批量处理结果',`<label>筛选结果 <select id="wf-result-filter"><option value="all">全部</option><option value="failed">失败与未完成</option><option value="done">成功</option><option value="skipped">未勾选</option></select></label><div id="wf-result-rows">${wfResultRows(result)}</div><button data-action="wf-retry">重新核对失败项</button><button data-action="close-modal">关闭</button>`,page);}
document.addEventListener('change',e=>{if(e.target.id==='wf-result-filter')$('#wf-result-rows').innerHTML=wfResultRows(view().batchResult,e.target.value);});

const labRenderBase=render;
render=function(){labRenderBase();if(S.page==='overview'&&WB.data?.search){const s=WB.data.search,f=s.freshness;$('.wb-searchbar')?.insertAdjacentHTML('afterend',`<div class="hint">正文索引 ${s.documents} 项；上次更新 ${esc(s.last?.at||'尚未建立')}；未覆盖 ${s.last?.remaining??'未知'} 项。${f?`元数据检查：新增 ${f.new}、变化 ${f.changed}、缺失 ${f.missing}（${esc(f.at)}）`:'尚未检查新旧状态'}<button data-action="lab-index-check">检查索引新旧状态</button></div>`);}if(S.page==='settings'&&RM.tab==='runtime')$('.wb-tools')?.insertAdjacentHTML('beforeend',`<details class="panel rm-editor"><summary>真实样本与规则/AI对比</summary><p>抽样不会自动把现有位置当正确答案。先确认标签，下面的AI评估只返回报告，不修改归属。</p><button data-action="lab-sample">抽取40项待标注清单</button><label>标签来源<select id="lab-source"><option value="user_supplied_labels">我已人工确认</option><option value="assistant_reviewed_reference">助手参考，非独立人工真值</option></select></label><textarea id="lab-cases" placeholder='[{"path":"相对路径","expected_target":"目标目录或null"}]'></textarea><button data-action="lab-rule">只读规则评估</button><button data-action="lab-ai">预览AI对比发送范围</button><button data-action="lab-candidates">从标签生成停用规则候选</button></details>`);};
const labPerformBase=perform;
perform=async function(action,page=S.page){
 const v=view(page);
 if(action==='lab-index-check'){const r=await job('wb-index-check',{},page);await refreshPage(page);note(`索引检查：新增${r.new}、变化${r.changed}、缺失${r.missing}。点击更新索引可继续增量处理。`,false,page);return;}
 if(action==='lab-sample'){const r=await job('wb-lab-sample',{limit:40},page);v.labSample=r;modal('待标注的真实样本',`<p>这些文件尚未标注。请逐项确认目标，无法确定时使用null。</p><pre>${esc(JSON.stringify(r,null,2))}</pre><button data-action="close-modal">关闭</button>`,page);return;}
 if(['lab-rule','lab-ai','lab-candidates'].includes(action)){
  const cases=JSON.parse($('#lab-cases').value),reference_source=$('#lab-source').value;
  if(action==='lab-rule'){const r=await job('wb-quality',{cases,reference_source},page);modal('规则参考评估',`<pre>${esc(JSON.stringify(r,null,2))}</pre><button data-action="close-modal">关闭</button>`,page);return;}
  if(action==='lab-candidates'){if(RM.dirty)await rmSave();const r=await job('wb-lab-rules',{cases},page);RM.edit=null;RM.dirty=false;note(`已生成${r.candidates}项停用候选，请在规则页核对、试跑后发布。`,false,page);return;}
  const r=await job('wb-lab-preview',{cases,reference_source},page);v.labTicket=r.ticket;
  modal('真实AI对比 · 发送确认',`<p>${r.samples}项 · ${esc(r.model)}。正确标签不发送给模型；原件和正式归属不修改。</p><pre>${esc(JSON.stringify(r.documents,null,2))}</pre><button data-action="lab-run">确认发送并对比</button><button data-action="close-modal">取消</button>`,page);return;
 }
 if(action==='lab-run'){const ticket=v.labTicket;v.labTicket=null;closeModal(page);const r=await job('wb-lab-run',{ticket},page);modal('规则与AI对比报告',`<p>仅代表本批及其标签来源，不外推全库。</p><pre>${esc(JSON.stringify(r,null,2))}</pre><button data-action="close-modal">关闭</button>`,page);return;}
 return labPerformBase(action,page);
};
