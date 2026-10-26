'use strict';
titles.ai=['AI 管理','管理 DeepSeek 连接、审核范围和调用记录。'];
const originalRender=render;
render=function(){
 if(S.page==='ai'){renderAI();return;}
 originalRender();
 const data=S.boot.ai;const localNode=$('.local');if(localNode)localNode.innerHTML='<i></i> 本机模式 · '+(S.boot.model_enabled?'DeepSeek已启用':'AI未启用');
 if(S.page==='inbox'){
  const count=S.rows.filter(r=>r.decision==='review').length;
  $('.toolbar')?.insertAdjacentHTML('beforeend',`<button data-action="ai-inbox" ${count?'':'disabled'}>✦ AI 审核待确认项${count?' ('+Math.min(count,data.settings.max_items)+')':''}</button>`);
 }
 if(S.page==='review')$('.toolbar')?.insertAdjacentHTML('beforeend',`<button data-action="ai-pending" ${S.pending.some(r=>r.available)?'':'disabled'}>✦ AI 审核待办</button>`);
 if(S.page==='settings'){
  const cards=$$('#content .settings-card');
  const card=cards.find(c=>c.querySelector('h2')?.textContent==='AI 审核接口');
  if(card)card.innerHTML=`<h2>DeepSeek AI 审核</h2><span class="badge ${S.boot.model_enabled?'green':''}">${S.boot.model_enabled?'已启用 · '+esc(data.settings.model):'未启用'}</span><p style="margin-top:12px">本机加密保存密钥，发送前预览选定资料；AI建议仍需预览计划后移动。</p><div class="toolbar"><button data-action="ai-page">打开 AI 管理</button></div>`;
 }
 if(S.page==='review'){
  const badgeNode=$('.toolbar .badge');if(badgeNode)badgeNode.textContent=S.boot.model_enabled?'DeepSeek 已接入 · 保留离线审核':'AI 未启用 · 可使用离线审核';
 }
 if(S.boot.ui_mode==='desktop')document.querySelectorAll('[data-action="shutdown-preview"]').forEach(b=>b.textContent='退出应用…');
};
function $$(selector){return Array.from(document.querySelectorAll(selector));}
function renderAI(){
 const a=S.boot.ai,c=a.settings;
 $('#content').innerHTML=`<div class="settings-grid"><div><div class="panel settings-card"><h2>DeepSeek 连接与密钥</h2><div class="status-banner">${a.configured?'已在本机加密保存密钥':'尚未保存密钥'} · ${c.enabled?'审核已启用':'审核已停用'}</div><div class="field"><label for="ai-key">API Key（留空保留现有密钥）</label><input id="ai-key" type="password" autocomplete="new-password" class="full-width" placeholder="${a.configured?'密钥已保存，不向页面返回原值':'输入 DeepSeek API Key'}"></div><div class="field"><label for="ai-model">模型</label><select id="ai-model" class="full-width">${a.models.map(m=>`<option ${m===c.model?'selected':''}>${esc(m)}</option>`).join('')}</select></div><label class="scope"><input id="ai-enabled" type="checkbox" ${c.enabled?'checked':''}>启用手动 AI 审核</label><div class="toolbar"><button class="primary" data-action="ai-save">保存配置</button><button data-action="ai-test" ${a.configured?'':'disabled'}>测试连接</button><button data-action="ai-remove-preview" ${a.configured?'':'disabled'}>移除密钥…</button></div><p class="hint">接口固定为 api.deepseek.com，密钥由Windows当前用户加密。不会保存到资料目录、代码或GitHub。</p>${a.connection?`<p class="hint">最近连接测试：${esc(a.connection.at.replace('T',' '))} · 成功</p>`:''}</div><div class="panel settings-card"><h2>发送范围与调用限制</h2><label class="scope"><input id="ai-content" type="checkbox" ${c.with_content?'checked':''}>允许发送选定资料的必要文字摘录</label><p class="hint">每项最多4000字；完整原文件不上传。关闭后只发送名称、上下文及候选目录。每次发送前都会显示预览。</p><div class="setting-row"><span>每批上限</span><select id="ai-max-items">${Array.from({length:10},(_,i)=>i+1).map(n=>`<option value="${n}" ${n===c.max_items?'selected':''}>${n} 项</option>`).join('')}</select></div><div class="setting-row"><span>每日审核上限</span><select id="ai-daily">${[5,10,20,30,50,100].map(n=>`<option value="${n}" ${n===c.daily_limit?'selected':''}>${n} 批</option>`).join('')}</select></div><p class="hint">限制随“保存配置”生效。后台规则整理不会自动调用AI；AI不执行文件操作、不改写长期分类规则。</p></div></div><div><div class="panel settings-card"><h2>调用情况</h2><div class="setting-row"><span>今日审核批次</span><strong>${a.calls_today} / ${c.daily_limit}</strong></div><div class="setting-row"><span>最近200批已记录Token</span><strong>${a.tokens_recorded.toLocaleString()}</strong></div><p class="hint">Token数量来自API实际返回，不估算费用。连接测试只查询模型列表。</p></div><div class="panel settings-card"><h2>最近审核记录</h2>${a.history.length?a.history.map(r=>`<details><summary>${esc(r.at.replace('T',' ').slice(0,19))} · ${r.items}项 · ${r.status==='completed'?'已返回结果':'未完成'}</summary><p class="hint">${esc(r.model)} · ${r.usage?.total_tokens||0} Token${r.error?' · '+esc(r.error):''}</p>${r.results?`<p class="hint">${r.results.map(x=>esc(labels[x.status]||x.status)).join('、')}</p>`:''}</details>`).join(''):empty('还没有AI审核记录','在收件箱或审核与待办中选择资料，预览发送范围后开始。')}</div></div></div>`;
}
const originalDetail=renderDetail;
renderDetail=function(){originalDetail();const r=S.focus;if(!r||r.data||r.available===false||r.status==='resolved_external')return;
 const area=$('#detail .detail-actions');if(!area)return;
 area.insertAdjacentHTML('beforeend','<button data-action="ai-item">✦ AI 审核此项</button>');
 if(r.details?.result?.decision==='suggest_reclassify')area.insertAdjacentHTML('beforeend','<button class="primary" data-action="ai-adopt">采用AI建议并预览</button>');
};
async function prepareAI(ids){
 const a=S.boot.ai;if(!a.configured||!a.settings.enabled){await load('ai');note('请先保存密钥并启用AI审核。');return;}
 const p=await job('ai-prepare',{ids});S.aiTicket=p.ticket;
 modal('发送至 DeepSeek · 审核预览',`<p>将发送以下 ${p.items.length} 项的名称、分类上下文、${p.target_count} 个现有候选目录${p.with_content?'及必要文字摘录':''}，使用 ${esc(p.model)}。原文件不上传。</p>${p.items.map(i=>`<div class="preview-item"><strong>${esc(i.name)}</strong><div class="path">${esc(i.source)}</div><details><summary>查看本项将发送的摘录与包成员</summary><pre>${esc(i.excerpt||'没有文字摘录')}${i.members.length?'\n\n'+esc(i.members.join('\n')):''}</pre></details>${i.warnings.map(w=>`<p class="hint">${esc(w)}</p>`).join('')}</div>`).join('')}<p class="hint">AI只返回审核意见和候选归属。采用位置建议后仍须预览并执行移动计划。</p><div class="modal-actions"><button data-action="close-modal">返回</button><button class="primary" data-action="ai-run">发送这 ${p.items.length} 项至 DeepSeek</button></div>`);
}
const originalPerform=perform;
perform=async function(action){
 if(action==='ai-page'){await load('ai');return;}
 if(action==='ai-save'){
  const body={enabled:$('#ai-enabled').checked,model:$('#ai-model').value,with_content:$('#ai-content').checked,max_items:Number($('#ai-max-items').value),daily_limit:Number($('#ai-daily').value)};
  if($('#ai-key').value.trim())body.api_key=$('#ai-key').value.trim();
  await job('ai-configure',body);$('#ai-key').value='';await load('ai');note('AI配置已保存。普通扫描和后台整理仍不会自动调用模型。');return;
 }
 if(action==='ai-test'){const r=await job('ai-test');await load('ai');note('DeepSeek连接成功，可用模型：'+r.models.join('、'));return;}
 if(action==='ai-remove-preview'){modal('移除本机API密钥','<p>移除后将停止新的AI审核；原资料和审核记录保留。可随时重新输入密钥。</p><div class="modal-actions"><button data-action="close-modal">返回</button><button data-action="ai-remove">确认移除</button></div>');return;}
 if(action==='ai-remove'){await job('ai-clear-key');$('#modal').close();await load('ai');note('本机保存的密钥已移除，AI已停用。');return;}
 if(action==='ai-inbox'){await prepareAI(S.rows.filter(r=>r.decision==='review').slice(0,S.boot.ai.settings.max_items).map(r=>r.id));return;}
 if(action==='ai-pending'){await prepareAI(S.pending.filter(r=>r.available).slice(0,S.boot.ai.settings.max_items).map(r=>r.id));return;}
 if(action==='ai-item'){await prepareAI([S.focus.id]);return;}
 if(action==='ai-run'){
  const ticket=S.aiTicket;S.aiTicket=null;$('#modal').close();const result=await job('ai-run',{ticket});await load('review');
  const rejected=result.results.filter(r=>r.status==='rejected');note(`AI已返回 ${result.results.length} 项审核结果，使用 ${result.usage.total_tokens||0} Token。文件尚未移动。`+(rejected.length?' 有'+rejected.length+'项因版本变化等原因被拒绝。':''),!!rejected.length);return;
 }
 if(action==='ai-adopt'){
  const p=await job('ai-adopt',{id:S.focus.id});S.preview=p;
  modal('采用AI建议 · 移动计划',`<p>你正在人工采用本次AI建议，请核对实际目标。</p>${p.actions.map(a=>`<div class="preview-item"><strong>${esc(filename(a.source))}</strong><div class="path">${esc(a.source)}</div><div class="arrow">↓</div>${esc(a.target)}<p class="hint">${esc(a.evidence)}</p></div>`).join('')}<div class="modal-actions"><button data-action="close-modal">返回</button><button class="primary" data-action="execute">执行这 ${p.actions.length} 项</button></div>`);return;
 }
 return originalPerform(action);
};
const originalRefreshStatus=refreshStatus;
refreshStatus=async function(){await originalRefreshStatus();const node=$('.local');if(node)node.innerHTML='<i></i> 本机模式 · '+(S.boot.model_enabled?'DeepSeek已启用':'AI未启用');const version=$('.sidebar-bottom small');if(version&&S.boot.app_version)version.textContent='v'+S.boot.app_version+' · 本机工作空间';};
