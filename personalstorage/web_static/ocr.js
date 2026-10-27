'use strict';
const ocrSources={native:'直接提取文字',local_ocr:'本地 OCR',mixed:'直接文字＋本地 OCR',none:'未取得可用文字'};
function ocrPanel(){
 const data=S.boot.ocr,c=data.settings;
 return `<div class="panel settings-card"><h2>本地 OCR 识别</h2><div class="status-banner">${data.ready?'中文 / 英文组件已就绪':'本地组件尚未就绪'} · ${c.enabled?'已启用':'已停用'}</div><p>${esc(data.reason)}</p><label class="scope"><input id="ocr-enabled" type="checkbox" ${c.enabled?'checked':''}>图片与扫描页启用本地识别</label><p class="hint">已有文字的 PDF 页面优先直接读取；扫描页再识别。只在本机运行，不调用 DeepSeek，也不修改原文件。</p><div class="setting-row"><span>每份文件最多页数 / 帧数</span><select id="ocr-pages">${[1,3,5,10,20].map(n=>`<option value="${n}" ${n===c.max_pages?'selected':''}>${n}</option>`).join('')}</select></div><div class="setting-row"><span>分类正文最低置信度</span><select id="ocr-confidence">${[.5,.7,.8,.85,.9,.95,.99].map(n=>`<option value="${n}" ${n===c.min_confidence?'selected':''}>${Math.round(n*100)}%</option>`).join('')}</select></div><div class="setting-row"><span>单文件解析最长时间</span><select id="ocr-timeout">${[15,30,60,120,180].map(n=>`<option value="${n}" ${n===c.timeout?'selected':''}>${n} 秒</option>`).join('')}</select></div><div class="toolbar"><button class="primary" data-action="ocr-save">保存 OCR 设置</button><button data-action="ocr-test" ${data.ready?'':'disabled'}>本机识别自检</button></div><p class="hint">低置信文字不作为规则分类正文。页数、大小或文字达到上限时会标记未完整读取；仍不确定的资料继续交给 AI 或人工。</p><div class="setting-row"><span>缓存条目 / 文字体积</span><strong>${data.cached_items} 项 / ${(data.cache_bytes/1024/1024).toFixed(2)} MB</strong></div><div class="setting-row"><span>累计缓存命中 / 已识别页帧</span><strong>${data.cache_hits} / ${data.recognized_pages}</strong></div><div class="toolbar"><button data-action="ocr-clear-preview">清空识别缓存…</button></div><p class="hint">缓存按文件内容、识别配置和引擎版本复用；移动或改名不会重复识别。缓存只保存在本机，最多2000项、约32MB文字，不随 Git 或便携包分发。</p>${data.cache_warning?`<p class="hint">${esc(data.cache_warning)}</p>`:''}</div>`;
}
const ocrPreviousRender=render;
render=function(){
 ocrPreviousRender();if(!S.boot?.ocr)return;
 if(S.page==='settings')$('#content .settings-grid>div')?.insertAdjacentHTML('afterbegin',ocrPanel());
 if(S.page==='inbox'&&S.rows.some(r=>r.ocr)){
  const rows=S.rows.filter(r=>r.ocr),hits=rows.filter(r=>r.ocr.cache_hit).length,recognized=rows.filter(r=>r.ocr.recognized_pages>0).length;
  $('#content').insertAdjacentHTML('beforeend',`<p class="hint" id="ocr-scan-summary">文字提取 / OCR ${rows.length} 项 · 含本地识别 ${recognized} 项 · 复用缓存 ${hits} 项。识别不调用 DeepSeek；结果仍按规则优先分类。</p>`);
 }
 syncTaskUI();
};
const ocrPreviousDetail=renderDetail;
renderDetail=function(){
 ocrPreviousDetail();const features=S.detail?.features,meta=features?.ocr||S.focus?.ocr;if(!meta||!$('#detail'))return;
 const source=features?.text_source||S.focus?.text_source||'none';
 $('#detail h3')?.insertAdjacentHTML('afterend',`<div class="status-banner" id="ocr-source">${esc(ocrSources[source]||source)}${meta.cache_hit?' · 缓存复用':''}${meta.status==='disabled'?' · OCR已停用':''}${meta.status==='error'||meta.status==='timeout'||meta.status==='unavailable'?' · 识别未完成':''}</div>`);
 if(meta.pages?.length)$('#detail .detail-actions')?.insertAdjacentHTML('beforebegin',`<details id="ocr-page-details"><summary>页 / 帧读取情况（${meta.processed_pages} / ${meta.total_pages}）</summary><div class="readonly-list">${meta.pages.map(p=>`<div>第 ${p.page} 页/帧 · ${p.source==='ocr'?'本地 OCR':p.source==='native'?'直接文字':'未取得文字'} · ${p.characters} 字${p.confidence!=null?' · 平均置信度 '+Math.round(p.confidence*100)+'%':''}${p.low_confidence_lines?' · '+p.low_confidence_lines+' 行未采用':''}</div>`).join('')}</div></details>`);
 if(S.focus?.available!==false&&S.focus?.kind!=='directory'&&S.detail)$('#detail .detail-actions')?.insertAdjacentHTML('beforeend','<button data-action="ocr-reread">重新读取（忽略缓存）</button>');
};
const ocrPreviousPerform=perform;
perform=async function(action,page=S.page){
 const v=view(page),focus=v.focus;
 if(action==='ocr-save'){
  const data={enabled:$('#ocr-enabled').checked,max_pages:Number($('#ocr-pages').value),min_confidence:Number($('#ocr-confidence').value),timeout:Number($('#ocr-timeout').value)};
  await job('ocr-configure',data,page);view('inbox').needsScan=true;await settled(page,'OCR设置已保存。下次扫描按新配置读取，原资料保持不变。');return;
 }
 if(action==='ocr-test'){const result=await job('ocr-test',{},page);await settled(page,`本机合成图片识别通过，用时 ${(result.elapsed_ms/1000).toFixed(1)} 秒；未联网、未调用 DeepSeek。`);return;}
 if(action==='ocr-clear-preview'){modal('清空识别缓存','<p>仅清除本机保存的文字提取结果，下次需要时重新读取。原图片、PDF、分类规则、手动关联和 API 密钥均保留。</p><div class="modal-actions"><button data-action="close-modal">返回</button><button data-action="ocr-clear-cache">确认清空缓存</button></div>',page);return;}
 if(action==='ocr-clear-cache'){closeModal(page);await job('ocr-clear-cache',{},page);view('inbox').needsScan=true;await settled(page,'本地识别缓存已清空，原资料保持不变。');return;}
 if(action==='ocr-reread'){
  const id=focus.id;const detail=await job('detail',{id,force_ocr:true},page);if(v.focus?.id===id){v.detail=detail;if(S.page===page)renderDetail();}return;
 }
 return ocrPreviousPerform(action,page);
};
