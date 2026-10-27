'use strict';
titles.settings=['规则管理','维护分类约定，先试跑，再发布。'];
$('nav [data-page="settings"]').innerHTML='<span>⚙</span>规则管理';
const RM={tab:'semantic',status:null,edit:null,dirty:false,revision:null,base:null,selected:0,category:0,task:0,query:'',filter:'all',validation:null,simulation:null,samples:[],testMode:'synthetic',synthetic:{name:'样本.txt',text:'',kind:'file'},context:{task_id:'',purpose:''}};
const rmClone=x=>JSON.parse(JSON.stringify(x));
const rmFields={name:'文件 / 资料包名称',text:'提取正文（含 OCR）',kind:'对象类型：文件 / 文件夹','context.task_id':'明确任务标识','context.purpose':'明确用途'};
const rmOps={equals:'等于',contains:'包含',in:'属于列表',regex:'正则（高级）'};
const rmTabs=[['semantic','分类约定'],['rules','自动分类规则'],['tasks','任务绑定'],['test','试跑与版本'],['runtime','运行设置']];
const rmID=()=>crypto.randomUUID().replaceAll('-','');
function rmDirty(){RM.dirty=true;RM.simulation=null;const el=$('#rm-draft-state');if(el)el.textContent='未保存修改';}
function rmAssign(data,force=false){
 RM.status=data;
 if(force||!RM.edit||(!RM.dirty&&RM.revision!==data.draft?.revision)){
  RM.edit=rmClone(data.draft?.model||data.live);RM.baseModel=rmClone(data.base_model||data.live);RM.revision=data.draft?.revision||null;RM.base=data.draft?.base_hash||data.policy_hash;RM.dirty=false;RM.validation=null;
 }
}
async function rmRefresh(force=false){rmAssign(await api('rules'),force);if(S.page==='settings')render();}
const rmPreviousRefresh=refreshPage;
refreshPage=async function(page=S.page){await rmPreviousRefresh(page);if(page==='settings')await rmRefresh();};
const rmPreviousRender=render;
render=function(){
 if(S.page!=='settings'){$('#content').classList.remove('rule-mode');rmPreviousRender();return;}
 if(RM.tab==='runtime'){
  rmPreviousRender();$$('#content .settings-card').find(c=>$('h2',c)?.textContent==='资料与分类规则')?.remove();
  $('#content').classList.remove('list-page');$('#content').classList.add('rule-mode');$('#content').insertAdjacentHTML('afterbegin',rmHeader());syncTaskUI();return;
 }
 $('#content').classList.remove('list-page');$('#content').classList.add('rule-mode');
 if(!RM.edit){$('#content').innerHTML=empty('正在读取规则','读取现行配置与本机草稿…');return;}
 $('#content').innerHTML=rmHeader()+(RM.tab==='semantic'?rmSemantic():RM.tab==='rules'?rmRules():RM.tab==='tasks'?rmTasks():rmTest());syncTaskUI();
};
const rmPreviousSync=syncTaskUI;
syncTaskUI=function(){rmPreviousSync();if(S.page==='settings')$$('[data-rm-write]').forEach(e=>e.disabled=!!view('settings').job);};
function rmButton(action,label,primary=false,extra=''){return `<button data-rm="${action}" data-rm-write ${primary?'class="primary"':''} ${extra}>${label}</button>`;}
function rmHeader(){
 const s=RM.status;
 return `<div class="rm-head"><div><strong>分类规则工作台</strong><span class="sub">生效 ${esc(s?.policy_version||S.boot.policy.policy_version)} · 通用包 ${esc(s?.package.version||'未登记')} · <span id="rm-draft-state">${RM.dirty?'未保存修改':RM.revision?'草稿已保存，尚未发布':'当前生效版本'}</span></span></div><div class="toolbar">${rmButton('save','保存草稿')}${rmButton('test-tab','样本试跑')}${rmButton('preview','查看差异并发布…',true)}</div></div><div class="rm-tabs" role="tablist" aria-label="规则管理页签">${rmTabs.map(([id,label])=>`<button data-rm="tab" data-tab="${id}" role="tab" aria-selected="${RM.tab===id}" class="${RM.tab===id?'active':''}">${label}</button>`).join('')}</div>${s?.external_changed||RM.base&&s?.policy_hash!==RM.base?'<div class="rm-alert">有效配置已在外部更新。当前草稿仍保留，请先比较新旧内容，再建立或合并到最新草稿。'+rmButton('external','比较外部修订…')+'</div>':''}${s?.warning?`<div class="rm-alert">${esc(s.warning)}</div>`:''}`;
}
function rmInput(label,value,attributes='',type='input'){
 return `<label class="rm-field"><span>${label}</span>${type==='textarea'?`<textarea data-rm-write ${attributes}>${esc(value)}</textarea>`:`<input data-rm-write ${attributes} value="${esc(value)}">`}</label>`;
}
function rmSelect(label,value,choices,attrs=''){
 return `<label class="rm-field"><span>${label}</span><select data-rm-write ${attrs}>${choices.map(([v,t])=>`<option value="${esc(v)}" ${v===value?'selected':''}>${esc(t)}</option>`).join('')}</select></label>`;
}
function rmChecks(){const v=RM.validation;return !v?'':`<div class="rm-checks ${v.valid?'good':'bad'}"><strong>${v.valid?'结构检查通过':'需要修正后才能发布'}</strong>${[...v.errors,...v.warnings].map(x=>`<p>${esc(x)}</p>`).join('')}${!v.errors.length&&!v.warnings.length?'<p>未发现结构或目标问题。样本测试仍不能证明所有文件都能正确分类。</p>':''}</div>`;}
function rmSemantic(){
 const config=RM.edit.config,cats=config.categories;RM.category=Math.min(RM.category,cats.length-1);const c=cats[RM.category];
 const subs=(config.subcategory_rules||[]).map((r,i)=>({r,i})).filter(({r})=>r.root===c.root||r.root.startsWith(c.root+'/'));
 const preferences=config.explicit_preferences||[];
 return `<div class="rm-grid rm-semantic"><aside class="panel rm-list"><div class="panel-head">分类职责 <small>固定架构</small></div>${cats.map((x,i)=>`<button data-rm="category" data-index="${i}" class="rm-list-item ${RM.category===i?'active':''}"><strong>${esc(x.root)}</strong><small>${esc(x.purpose||'查看分类边界')}</small></button>`).join('')}<p class="hint rm-pad">编号与固定目录只读；描述和细分建议可作为本地草稿维护。</p></aside><section class="panel rm-editor"><div class="rm-section-head"><h2>${esc(c.root)}</h2>${rmButton('defaults','恢复此类通用描述…')}</div><span class="badge">${rmIsLocal(c)?'本地修改':'通用描述'} · 供人工与 AI 理解</span>${rmInput('收什么 / 用途',c.purpose||'',`data-rm-category="purpose"`,'textarea')}${rmInput('不收什么 / 分类边界',c.boundary||'',`data-rm-category="boundary"`,'textarea')}<p class="hint">更改描述不创建或移动目录；自然语言不会直接变成脚本执行条件。</p>${subs.map(({r,i})=>`<div class="rm-subcard"><h3>${esc(r.root)}</h3>${rmInput('建议内部结构',r.layout||'',`data-rm-sub="${i}" data-key="layout"`)}${rmInput('判定门槛与边界',r.gate||'',`data-rm-sub="${i}" data-key="gate"`,'textarea')}${r.suggested_children?rmInput('建议子类（每行一项，不自动创建）',r.suggested_children.join('\n'),`data-rm-sub="${i}" data-key="suggested_children"`,'textarea'):''}${r.suggested_topics?rmInput('建议主题（每行一项）',r.suggested_topics.join('\n'),`data-rm-sub="${i}" data-key="suggested_topics"`,'textarea'):''}</div>`).join('')}<details class="rm-subcard"><summary>分类判断顺序</summary><p class="hint">这里是语义判断顺序，与自动规则的决策层级分别管理。安全和归档约定受保护。</p>${(config.classification_rules||[]).map((r,i)=>`<div class="rm-subcard"><strong>${esc(r.id)} · 顺序 ${r.priority} ${r.id.startsWith('S')?'🔒':''}</strong>${r.id.startsWith('S')?`<p>${esc(r.condition)}</p><p>${esc(r.action)}</p>`:rmInput('条件说明',r.condition,`data-rm-semantic="${i}" data-key="condition"`,'textarea')+rmInput('处理约定',r.action,`data-rm-semantic="${i}" data-key="action"`,'textarea')}</div>`).join('')}</details><div class="rm-section-head"><h2>长期偏好与例外</h2>${Array.isArray(preferences)?rmButton('pref-add','＋ 添加偏好'):''}</div><p class="hint">单份文件的审核说明不会自动变成长期偏好。发布后在线 AI 才读取这里的约定；原有私人 Markdown 保留，可对照后手工迁入。</p>${RM.status.legacy_document?`<details class="rm-subcard"><summary>对照原有私人 Markdown（只读参考）</summary><pre>${esc(RM.status.legacy_document)}</pre></details>`:''}${Array.isArray(preferences)?preferences.map((r,i)=>`<div class="rm-subcard">${typeof r==='string'?rmInput('原有偏好',r,`data-rm-pref="${i}" data-key="string"`,'textarea'):!Object.hasOwn(r,'description')?`<strong>兼容偏好 · 原样保留</strong><p class="hint">此条目使用已有的专用结构，在线 AI 继续读取完整内容。此版本以只读形式展示，可另建偏好草稿补充说明。</p><pre>${esc(JSON.stringify(r,null,2))}</pre>`:['description','scope','exception','evidence'].map(k=>rmInput(({description:'偏好说明',scope:'适用范围',exception:'例外',evidence:'确认依据'})[k],r[k]||'',`data-rm-pref="${i}" data-key="${k}"`,'textarea')).join('')}${rmButton('pref-remove','移出草稿',false,`data-index="${i}"`)}</div>`).join('')||'<p class="hint">尚无额外偏好。</p>':rmInput('原有偏好（保留原格式）',typeof preferences==='string'?preferences:JSON.stringify(preferences,null,2),'id="rm-legacy-pref"','textarea')}${rmChecks()}</section></div>`;
}
function rmIsLocal(category){const d=RM.status?.defaults?.categories?.find(x=>x.root===category.root);return !d||d.purpose!==category.purpose||d.boundary!==category.boundary;}
function rmRules(){
 const rules=RM.edit.rules;RM.selected=Math.max(0,Math.min(RM.selected,rules.length-1));const row=rules[RM.selected],r=row?.rule;
 return `<div class="rm-grid rm-three"><aside class="panel rm-list"><div class="panel-head">明确执行条件 <small>${rules.filter(x=>x.enabled).length} 条启用</small></div><div class="rm-pad"><input id="rm-query" placeholder="搜索规则或目标" value="${esc(RM.query)}"><select id="rm-filter"><option value="all">全部规则</option><option value="enabled" ${RM.filter==='enabled'?'selected':''}>启用</option><option value="disabled" ${RM.filter==='disabled'?'selected':''}>停用</option></select>${rmButton('rule-add','＋ 新建规则',true)}</div><div id="rm-rule-list">${rmRuleList()}</div></aside><section class="panel rm-editor">${!r?empty('从一条明确规则开始','配置文件保持完整，现有字段不会被空模板替换。'):`<div class="rm-section-head"><h2>编辑条件</h2><span class="badge">${row.enabled?'启用后随发布生效':'已停用 / 草稿'}</span></div>${rmInput('显示名称',row.title,'data-rm-row="title"')}${rmInput('稳定标识（创建后保持不变）',r.id,'readonly')}${rmInput('说明',row.note,'data-rm-row="note"','textarea')}<div class="rm-inline">${rmInput('决策层级：数字越小越优先',r.level??100,'data-rm-rule="level" type="number" step="1"')}<label class="scope"><input type="checkbox" data-rm-write id="rm-enabled" ${row.enabled?'checked':''}>发布时启用</label></div><h3>当：以下条件全部满足</h3><p class="hint">正文包含原生提取与 OCR。类型只区分文件/文件夹；任务和用途上下文必须明确提供。</p>${r.all.map((c,i)=>`<div class="rm-condition">${rmSelect('字段',c.field,Object.entries(rmFields),`data-rm-condition="${i}" data-key="field"`)}${rmSelect('判断',c.op,Object.entries(rmOps),`data-rm-condition="${i}" data-key="op"`)}${c.field==='kind'&&c.op==='equals'?rmSelect('值',c.value,[['file','文件'],['directory','文件夹']],`data-rm-condition="${i}" data-key="value"`):rmInput(c.op==='in'?'值（每行一项）':'值',Array.isArray(c.value)?c.value.join('\n'):c.value,`data-rm-condition="${i}" data-key="value"`,c.op==='in'?'textarea':'input')}<button data-rm="condition-remove" data-rm-write data-index="${i}" aria-label="删除第${i+1}个条件">×</button>${c.op==='regex'?'<p class="hint">高级正则最长256字符，隔离匹配最长2秒；超时进入待确认。复杂表达式请先试跑。</p>':''}</div>`).join('')}${rmButton('condition-add','＋ 同时满足一个条件')}<h3>则：归入已有目录</h3><div class="target-box">${esc(r.target_parent||'尚未选择目标')}</div><div class="toolbar">${rmButton('rule-target','选择目标目录…')}${rmButton('rule-copy','复制为新规则')}${rmButton('rule-remove','移出草稿')}</div>${rmChecks()}`}</section><aside class="panel rm-explain"><h3>执行逻辑</h3><ol><li>明确任务上下文先检查任务绑定与状态。</li><li>收集所有条件满足的规则。</li><li>仅采用数字最小的决策层级。</li><li>同层多个目标进入待确认。</li></ol><p>规则列表顺序不决定优先级。</p><h3>发布边界</h3><p>保存草稿不生效。发布只改规则，不移动文件。</p><p>文件变化、同名冲突、归档状态和完整包保护继续由原执行流程检查。</p><h3>覆盖检查</h3><p>先用代表样本比较现行版与草稿。有限样本不能保证所有规则都没有冲突。</p>${rmButton('validate','检查草稿')}${rmButton('test-tab','打开样本试跑')}</aside></div>`;
}
function rmRuleList(){return RM.edit.rules.map((row,i)=>({row,i})).filter(({row})=>(RM.filter==='all'||row.enabled===(RM.filter==='enabled'))&&JSON.stringify([row.title,row.rule]).toLowerCase().includes(RM.query.toLowerCase())).sort((a,b)=>(a.row.rule.level??100)-(b.row.rule.level??100)).map(({row,i})=>`<button data-rm="rule-select" data-index="${i}" class="rm-list-item ${RM.selected===i?'active':''}"><strong>${esc(row.title||row.rule.id)}</strong><small>层级 ${row.rule.level??100} · ${row.enabled?'启用':'停用'}</small><small>${esc(row.rule.target_parent||'目标未设置')}</small></button>`).join('')||'<p class="hint rm-pad">暂无匹配规则。</p>';}
function rmTasks(){
 const tasks=RM.edit.config.tasks||[],t=tasks[RM.task];
 return `<div class="rm-grid"><aside class="panel rm-list"><div class="panel-head">任务与事项 <small>${tasks.length} 个绑定</small></div><div class="rm-pad">${rmButton('task-add','＋ 登记任务',true)}</div>${tasks.map((x,i)=>`<button data-rm="task-select" data-index="${i}" class="rm-list-item ${RM.task===i?'active':''}"><strong>${esc(x.title||x.id)}</strong><small>${esc(x.path)}</small><small>${esc(x.lifecycle||'unspecified')} · ${esc(x.storage||'未登记')}</small></button>`).join('')}</aside><section class="panel rm-editor"><h2>已有任务目录绑定</h2><p class="hint">绑定不创建目录、不改名。它用于明确任务上下文，不会仅凭模糊关键词推断文件的任务。</p>${t?`${rmInput('任务名称',t.title||t.id,'data-rm-task="title"')}${rmInput('唯一任务标识',t.id,'data-rm-task="id"')}${rmInput('用途说明',t.purpose||'','data-rm-task="purpose"','textarea')}<div class="target-box">${esc(t.path||'尚未选择任务目录')}</div>${rmButton('task-target','选择已有任务目录…')}${rmSelect('登记状态（不自动改文件夹名）',t.lifecycle,[['active','进行中'],['unspecified','未确认'],['completed','已完成'],['archived','已归档']],'data-rm-task="lifecycle"')}<p class="hint">存储状态：${esc(t.storage)}。移动硬盘和离线工作流本轮保留。</p>${rmButton('task-remove','移出绑定草稿')}`:empty('选择或登记一个任务','课程、项目、竞赛和事项均可绑定现有目录。')}${rmButton('validate','检查绑定与目录状态')}${rmChecks()}</section></div>`;
}
function rmTrace(trace){
 const r=trace.result;
 return `<div class="target-box">${r.decision==='move'?'建议归属：'+esc(r.target_parent):'待确认：'+esc(r.reason)}${trace.selected_level!==null?`<small>决策层级 ${trace.selected_level}</small>`:''}</div>${trace.target_issues.map(x=>`<p class="hint">${esc(x)}</p>`).join('')}${trace.rules.map(rule=>`<details class="rm-trace ${rule.matched?'matched':''}"><summary>${rule.matched?'✓ 命中':'○ 未满足'} ${esc(rule.id)} · 层级 ${rule.level}${rule.matched&&rule.level!==trace.selected_level?' · 低优先级未采用':''}</summary><p class="path">${esc(rule.target_parent)}</p>${rule.conditions.map(c=>`<p>${c.matched?'✓':'×'} ${esc(rmFields[c.field])} ${esc(rmOps[c.op])} ${esc(JSON.stringify(c.value))}<small>${c.present?'读取到：'+esc(c.actual):'本次没有该字段，不参与命中'}</small></p>`).join('')}</details>`).join('')}`;
}
function rmTest(){
 const s=RM.simulation;
 return `<div class="rm-test"><section class="panel rm-editor"><div class="rm-section-head"><h2>样本试跑</h2><span class="badge green">本地运行 · 不调用 AI</span></div><div class="toolbar"><button data-rm="sample-mode" data-mode="synthetic" class="${RM.testMode==='synthetic'?'primary':''}">模拟样本</button><button data-rm="sample-mode" data-mode="real" class="${RM.testMode==='real'?'primary':''}">已有文件 / 资料包</button></div>${RM.testMode==='synthetic'?`<div class="rm-inline">${rmInput('模拟名称',RM.synthetic.name,'data-rm-synthetic="name"')}${rmSelect('对象类型',RM.synthetic.kind,[['file','文件'],['directory','文件夹']],'data-rm-synthetic="kind"')}</div>${rmInput('模拟提取正文（最多16000字符）',RM.synthetic.text,'data-rm-synthetic="text" maxlength="16000"','textarea')}`:`<div class="toolbar">${rmButton('sample-pick','选择库内或已登记收件箱样本…')}</div>${RM.samples.map((x,i)=>`<div class="rm-sample"><span>${esc(x.path)}</span><button data-rm="sample-remove" data-rm-write data-index="${i}">移除</button></div>`).join('')||'<p class="hint">最多选择10个样本。文件夹保持整体，不递归拆分正文。</p>'}`}<div class="rm-inline">${rmInput('本次明确任务标识（可选）',RM.context.task_id,'data-rm-context="task_id"')}${rmInput('本次明确用途（可选）',RM.context.purpose,'data-rm-context="purpose"')}</div><div class="toolbar">${rmButton('simulate','保存并试跑',true)}${rmButton('validate','仅检查草稿')}</div><p class="hint">只读取选中样本并复用本地提取 / OCR，不移动、不新增待办。影响范围只代表本次样本。</p>${rmChecks()}${s?`<div class="status-banner">检查 ${s.results.length} 个样本 · 归属结果变化 ${s.test.changed} 项 · 未检查的资料影响未知</div>${s.results.map(x=>`<article class="rm-result"><h3>${esc(x.name)}</h3><p class="hint">文字来源：${esc(typeof ocrSources!=='undefined'?ocrSources[x.text_source]||x.text_source:x.text_source)}${x.binding.synthetic?' · 模拟特征':' · 真实样本'}</p>${x.warnings.map(w=>`<p class="hint">${esc(w)}</p>`).join('')}${x.ocr?`<details><summary>OCR 页帧与提取信息</summary><pre>${esc(JSON.stringify(x.ocr,null,2))}</pre></details>`:''}<details><summary>文字摘录</summary><pre>${esc(x.excerpt||'没有可用正文')}</pre></details><div class="rm-comparison"><div><h4>现行规则</h4>${rmTrace(x.current)}</div><div><h4>草稿规则</h4>${rmTrace(x.draft)}</div></div></article>`).join('')}`:RM.status?.draft?.test?`<p class="hint">已保存试跑摘要：${esc(RM.status.draft.test.at)} · ${RM.status.draft.test.count} 个样本。完整正文与轨迹不持久保存。</p>`:''}</section><aside class="panel rm-editor"><h2>规则版本</h2><p class="hint">与七天操作记录独立，保留最近50次发布。回退会先建立草稿，重新检查和发布；不会把文件搬回旧位置。</p>${(RM.status.history||[]).map(h=>`<div class="rm-version"><strong>${esc(h.policy_version)}</strong><small>${esc(h.at)} · ${esc(h.source)}</small><p>${esc(h.summary)}</p>${rmButton('restore','从此版本建立草稿…',false,`data-id="${h.id}"`)}</div>`).join('')||'<p class="hint">首次保存草稿时记录当前有效配置。</p>'}${rmButton('reset','重新载入现行规则…')}<hr><h3>供人和其他 AI 阅读</h3><p class="hint">阅读版由已发布配置生成，包含本机私人约定，不随公开代码发布。</p>${RM.status.reading_pending?'<span class="badge amber">阅读版待生成</span>':''}${rmButton('reading','生成 / 更新规则阅读版')}</aside></div>`;
}
async function rmSave(){
 if(!RM.dirty&&RM.revision)return;
 const result=await job('rules-save',{model:rmClone(RM.edit),revision:RM.revision,base_hash:RM.base},'settings');
 RM.revision=result.draft.revision;RM.base=result.draft.base_hash;RM.edit=rmClone(result.draft.model);RM.dirty=false;RM.validation=result.validation;
 RM.status.draft=result.draft;if(S.page==='settings')render();
}
async function rmTarget(kind){
 const stack=[];let current=null;
 async function draw(id){
  const data=await api('directories?archived='+(kind==='task')+(id?'&id='+encodeURIComponent(id):''));current=id?{id,path:data.path}:null;
  modal('选择规则目标目录',`<p>仅绑定现有目录，不创建或移动文件。</p><div class="toolbar"><button id="rm-dir-back" ${stack.length?'':'disabled'}>← 上一级</button><span class="pick-path">${esc(data.path||'资料分类')}</span></div><div class="directory-list">${data.items.map(x=>`<button data-rm-dir="${x.id}">▱ ${esc(x.name)}</button>`).join('')}</div><div class="modal-actions"><button data-action="close-modal">取消</button><button id="rm-dir-use" class="primary" ${current?'':'disabled'}>绑定此目录</button></div>`,'settings');
  if(S.page!=='settings')return;
  $('#rm-dir-back').onclick=()=>draw(stack.pop()).catch(handleError);
  $$('[data-rm-dir]').forEach(b=>b.onclick=()=>{stack.push(id);draw(b.dataset.rmDir).catch(handleError);});
  $('#rm-dir-use').onclick=()=>{if(kind==='rule')RM.edit.rules[RM.selected].rule.target_parent=current.path;else RM.edit.config.tasks[RM.task].path=current.path;rmDirty();closeModal('settings');render();};
 }
 await draw();
}
async function rmPickSamples(onDone=null){
 const stack=[];let selected=new Map(RM.samples.map(x=>[x.path,x]));
 async function draw(id,offset=0){
  const data=await api('rules-samples?offset='+offset+(id?'&id='+encodeURIComponent(id):''));
  modal('选择试跑样本',`<p>最多10个，文件夹按完整资料包测试。已选 <span id="rm-picked-count">${selected.size}</span> 个。</p><div class="toolbar"><button id="rm-sample-back" ${stack.length?'':'disabled'}>← 上一级</button><span class="pick-path">${esc(data.path||'资料库和已登记收件箱')}</span></div><div class="rm-browser">${data.items.map(x=>`<div class="rm-browser-row">${x.sampleable?`<input type="checkbox" data-rm-sample="${x.id}" aria-label="选择样本 ${esc(x.name)}" ${selected.has(x.path)?'checked':''}>`:'<span></span>'}${x.kind==='directory'?`<button data-rm-folder="${x.id}">▱ ${esc(x.name)} ›</button>`:`<span>${esc(x.name)}</span>`}</div>`).join('')||'<p>没有可选项目。</p>'}</div>${data.limited?'<p class="hint">仅显示当前目录前2000项，请进入更具体的目录。</p>':''}<div class="toolbar">${offset?'<button id="rm-sample-prev">上一页</button>':''}${data.next_offset!==null?'<button id="rm-sample-next">下一页</button>':''}</div><div class="modal-actions"><button data-action="close-modal">取消</button><button id="rm-sample-use" class="primary">使用已选样本</button></div>`,'settings');
  if(S.page!=='settings')return;
  $('#rm-sample-back').onclick=()=>{const p=stack.pop();draw(p.id,p.offset).catch(handleError);};
  $$('[data-rm-folder]').forEach(b=>b.onclick=()=>{stack.push({id,offset});draw(b.dataset.rmFolder).catch(handleError);});
  $$('[data-rm-sample]').forEach(b=>b.onchange=()=>{const x=data.items.find(x=>x.id===b.dataset.rmSample);if(b.checked){if(selected.size>=10){b.checked=false;note('每次最多10个样本。',true,'settings');return;}selected.set(x.path,x);}else selected.delete(x.path);$('#rm-picked-count').textContent=selected.size;});
  if($('#rm-sample-prev'))$('#rm-sample-prev').onclick=()=>draw(id,Math.max(0,offset-100)).catch(handleError);
  if($('#rm-sample-next'))$('#rm-sample-next').onclick=()=>draw(id,data.next_offset).catch(handleError);
  $('#rm-sample-use').onclick=()=>{RM.samples=[...selected.values()];closeModal('settings');render();if(onDone)onDone(RM.samples);};
 }
 await draw();
}
async function rmAct(action,button){
 if(action==='tab'){RM.tab=button.dataset.tab;render();return;}
 if(view('settings').job)return;
 if(action==='save'){await rmSave();note('草稿已保存，当前有效规则保持原状。',false,'settings');}
 if(action==='category'){RM.category=Number(button.dataset.index);render();}
 if(action==='rule-select'){RM.selected=Number(button.dataset.index);render();}
 if(action==='task-select'){RM.task=Number(button.dataset.index);render();}
 if(action==='sample-mode'){RM.testMode=button.dataset.mode;render();}
 if(action==='test-tab'){RM.tab='test';render();}
 if(action==='rule-add'){
  const id='rule_'+rmID().slice(0,12);RM.edit.rules.push({rule:{id,level:100,all:[{field:'name',op:'contains',value:''}],target_parent:''},enabled:true,title:'新规则',note:''});RM.selected=RM.edit.rules.length-1;rmDirty();render();
 }
 if(action==='rule-copy'){const row=rmClone(RM.edit.rules[RM.selected]);row.rule.id='rule_'+rmID().slice(0,12);row.title+='（副本）';RM.edit.rules.push(row);RM.selected=RM.edit.rules.length-1;rmDirty();render();}
 if(action==='rule-remove'){RM.edit.rules.splice(RM.selected,1);rmDirty();render();}
 if(action==='condition-add'){RM.edit.rules[RM.selected].rule.all.push({field:'text',op:'contains',value:''});rmDirty();render();}
 if(action==='condition-remove'){RM.edit.rules[RM.selected].rule.all.splice(Number(button.dataset.index),1);rmDirty();render();}
 if(action==='rule-target')await rmTarget('rule');
 if(action==='task-target')await rmTarget('task');
 if(action==='task-add'){RM.edit.config.tasks.push({id:'task_'+rmID().slice(0,12),title:'新任务',path:'',purpose:'',storage:'active',lifecycle:'active'});RM.task=RM.edit.config.tasks.length-1;rmDirty();render();}
 if(action==='task-remove'){RM.edit.config.tasks.splice(RM.task,1);rmDirty();render();}
 if(action==='pref-add'){(RM.edit.config.explicit_preferences||= []).push({id:'pref_'+rmID().slice(0,12),description:'',scope:'',exception:'',evidence:''});rmDirty();render();}
 if(action==='pref-remove'){RM.edit.config.explicit_preferences.splice(Number(button.dataset.index),1);rmDirty();render();}
 if(action==='sample-pick')await rmPickSamples();
 if(action==='sample-remove'){RM.samples.splice(Number(button.dataset.index),1);render();}
 if(action==='validate'){await rmSave();RM.validation=await job('rules-validate',{revision:RM.revision},'settings');if(S.page==='settings')render();}
 if(action==='simulate'){
  const context=Object.fromEntries(Object.entries(RM.context).filter(([,v])=>v.trim()));const samples=RM.testMode==='synthetic'?[{features:rmClone(RM.synthetic),context}]:RM.samples.map(x=>({id:x.id,context}));
  if(!samples.length)throw Error('请先选择样本。');await rmSave();RM.simulation=await job('rules-simulate',{revision:RM.revision,samples},'settings');
  await rmRefresh();note('本地试跑完成；未移动文件，也未调用 AI。',false,'settings');
 }
 if(action==='preview'){
  await rmSave();const result=await job('rules-preview',{revision:RM.revision},'settings');
  modal('发布规则 · 差异确认',`<p>${result.metadata_only?'仅更新规则名称、备注等管理信息。':'发布后旧整理计划和 AI 发送预览失效，持续后台授权撤销；下次整理使用新规则。'}</p>${result.changes.map(c=>`<details><summary>${esc(c.field)}</summary><div class="rm-comparison"><div><h4>当前</h4><pre>${esc(JSON.stringify(c.before,null,2))}</pre></div><div><h4>发布后</h4><pre>${esc(JSON.stringify(c.after,null,2))}</pre></div></div></details>`).join('')}${result.metadata_changes?`<details><summary>名称、备注与启停管理信息差异</summary><pre>${esc(JSON.stringify(result.metadata_changes,null,2))}</pre></details>`:''}${result.validation.warnings.map(x=>`<p class="hint">${esc(x)}</p>`).join('')}<p class="hint">${result.test?'试跑过 '+result.test.count+' 个样本；其余资料影响未评估。':'本次未改变执行条件；没有全库影响评估。'}<br>规则发布不移动文件。发布前自动备份。</p><div class="modal-actions"><button data-action="close-modal">返回草稿</button><button class="primary" data-rm="publish" data-rm-write data-ticket="${result.ticket}">确认发布规则</button></div>`,'settings');
 }
 if(action==='publish'){const result=await job('rules-publish',{ticket:button.dataset.ticket},'settings');closeModal('settings');RM.dirty=false;RM.edit=null;RM.simulation=null;await refreshStatus();await rmRefresh(true);note(result.warning||`规则已发布：${result.version}${result.effective_changed?'。旧计划失效，后台需重新授权。':'。仅管理信息更新。'}`,!!result.warning,'settings');}
 if(action==='restore'||action==='reset')modal('建立规则草稿',`<p>将${action==='restore'?'历史版本':'当前有效配置'}载入为新草稿，替换尚未发布的编辑。有效规则与文件位置保持原状。</p><div class="modal-actions"><button data-action="close-modal">取消</button><button class="primary" data-rm="reset-confirm" data-id="${esc(button.dataset.id||'')}">确认建立草稿</button></div>`,'settings');
 if(action==='reset-confirm'){await job('rules-reset',{revision:RM.revision,history_id:button.dataset.id||null},'settings');closeModal('settings');RM.edit=null;RM.dirty=false;await rmRefresh(true);}
 if(action==='reading'){await job('rules-reading',{},'settings');await rmRefresh();note('已从当前有效配置生成本地规则阅读版。',false,'settings');}
 if(action==='defaults')modal('恢复此类通用描述',`<p>仅将本类别的用途、边界及细分建议恢复为通用模板，保存到草稿。已有目录、任务和自动规则保持原状。</p><div class="modal-actions"><button data-action="close-modal">取消</button><button class="primary" data-rm="defaults-confirm">载入通用描述</button></div>`,'settings');
 if(action==='defaults-confirm'){
  const c=RM.edit.config.categories[RM.category],d=RM.status.defaults.categories.find(x=>x.root===c.root);if(!d)throw Error('此类别没有对应通用默认值');
  for(const k of ['purpose','boundary'])c[k]=d[k];RM.edit.config.subcategory_rules=(RM.edit.config.subcategory_rules||[]).map(x=>x.root===c.root||x.root.startsWith(c.root+'/')?rmClone(RM.status.defaults.subcategory_rules.find(y=>y.root===x.root)||x):x);rmDirty();closeModal('settings');render();
 }
 if(action==='external')modal('外部修订与当前草稿',`<p>有效配置已变化。可以尝试三方合并；存在同字段冲突时保留草稿并列出冲突，不自动覆盖。</p><details><summary>最新有效配置</summary><pre>${esc(JSON.stringify(RM.status.live,null,2))}</pre></details><details><summary>当前草稿</summary><pre>${esc(JSON.stringify(RM.edit,null,2))}</pre></details><div class="modal-actions"><button data-action="close-modal">返回</button><button data-rm="rebase">合并到最新有效配置</button></div>`,'settings');
 if(action==='rebase'){const mergeBody={revision:RM.revision,model:rmClone(RM.edit),base_model:RM.baseModel,base_hash:RM.base};const result=await job('rules-rebase',mergeBody,'settings');if(result.conflicts?.length){modal('需要手工解决的规则冲突',`<p>以下字段双方都有修改。当前草稿完整保留。请对照后选择保留草稿字段或采用最新字段，再重新检查。</p>${result.conflicts.map((c,i)=>`<div class="rm-subcard"><strong>${esc(c.field)}</strong><div class="rm-comparison"><pre>${esc(JSON.stringify(c.ours,null,2))}</pre><pre>${esc(JSON.stringify(c.theirs,null,2))}</pre></div><select data-rm-resolution="${i}"><option value="ours">保留草稿字段</option><option value="theirs">采用最新有效字段</option></select></div>`).join('')}<div class="modal-actions"><button data-action="close-modal">返回</button><button class="primary" id="rm-resolve-merge">按所选解决冲突</button></div>`,'settings');$('#rm-resolve-merge').onclick=async()=>{try{const resolutions=Object.fromEntries(result.conflicts.map((c,i)=>[c.field,$(`[data-rm-resolution="${i}"]`).value]));await job('rules-rebase',{...mergeBody,resolutions},'settings');closeModal('settings');await rmRefresh(true);}catch(e){note(e.message,true,'settings');}};}else{closeModal('settings');await rmRefresh(true);}}
}
document.addEventListener('click',e=>{const b=e.target.closest('[data-rm]');if(b){e.preventDefault();rmAct(b.dataset.rm,b).catch(error=>note(error.message||String(error),true,'settings'));}});
function rmEditEvent(e){
 const el=e.target;if(!RM.edit)return;
 if(el.id==='rm-query'){RM.query=el.value;$('#rm-rule-list').innerHTML=rmRuleList();return;}
 if(el.id==='rm-filter'){RM.filter=el.value;$('#rm-rule-list').innerHTML=rmRuleList();return;}
 let changed=true;
 if(el.dataset.rmRow)RM.edit.rules[RM.selected][el.dataset.rmRow]=el.value;
 else if(el.dataset.rmRule)RM.edit.rules[RM.selected].rule[el.dataset.rmRule]=Number(el.value);
 else if(el.id==='rm-enabled')RM.edit.rules[RM.selected].enabled=el.checked;
 else if(el.dataset.rmCondition!==undefined){const c=RM.edit.rules[RM.selected].rule.all[Number(el.dataset.rmCondition)],key=el.dataset.key;c[key]=key==='value'&&c.op==='in'?el.value.split('\n').filter(x=>x.trim()):el.value;if(key==='op'){c.value=c.op==='in'?[String(Array.isArray(c.value)?c.value[0]||'':c.value)]:Array.isArray(c.value)?c.value[0]||'':c.value;}if(key==='field'&&c.field==='kind'&&c.op==='equals')c.value='file';if(key!=='value'){rmDirty();render();return;}}
 else if(el.dataset.rmCategory)RM.edit.config.categories[RM.category][el.dataset.rmCategory]=el.value;
 else if(el.dataset.rmSub!==undefined)RM.edit.config.subcategory_rules[Number(el.dataset.rmSub)][el.dataset.key]=el.dataset.key.startsWith('suggested_')?el.value.split('\n').filter(x=>x.trim()):el.value;
 else if(el.dataset.rmSemantic!==undefined)RM.edit.config.classification_rules[Number(el.dataset.rmSemantic)][el.dataset.key]=el.value;
 else if(el.dataset.rmTask)RM.edit.config.tasks[RM.task][el.dataset.rmTask]=el.value;
 else if(el.dataset.rmPref!==undefined){const i=Number(el.dataset.rmPref);if(el.dataset.key==='string')RM.edit.config.explicit_preferences[i]=el.value;else RM.edit.config.explicit_preferences[i][el.dataset.key]=el.value;}
 else if(el.id==='rm-legacy-pref'){if(typeof RM.edit.config.explicit_preferences==='string')RM.edit.config.explicit_preferences=el.value;else{try{RM.edit.config.explicit_preferences=JSON.parse(el.value);}catch(_){return;}}}
 else if(el.dataset.rmSynthetic){RM.synthetic[el.dataset.rmSynthetic]=el.value;changed=false;}
 else if(el.dataset.rmContext){RM.context[el.dataset.rmContext]=el.value;changed=false;}
 else changed=false;
 if(changed)rmDirty();
}
document.addEventListener('input',e=>{if(!e.target.matches('select,input[type=checkbox]'))rmEditEvent(e);});
document.addEventListener('change',e=>{if(e.target.matches('select,input[type=checkbox]'))rmEditEvent(e);});
window.addEventListener('beforeunload',e=>{if(RM.dirty){e.preventDefault();e.returnValue='';}});
