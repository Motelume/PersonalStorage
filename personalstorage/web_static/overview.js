'use strict';
titles.overview=['目录总览','用关系图看清结构与联系，也可切换为目录树。'];
const OV={nodes:new Map(),children:new Map(),next:new Map(),expanded:new Set(['']),positions:{},links:[],mode:'graph',motion:true,files:false,manual:true,focus:'',selected:'',camera:{x:0,y:0,zoom:1},ready:false,results:null,searchSeq:0,query:'',showDetail:false};
const ovColors=['#64a6ff','#58c6a8','#e6b34d','#ac8bea','#e98ca8','#66b9cf'];
const ovParent=p=>p.includes('/')?p.slice(0,p.lastIndexOf('/')):'';
const ovName=p=>OV.nodes.get(p)?.name||p.split('/').pop()||S.boot?.status.root.split(/[\\/]/).pop()||'PersonalStorage';
const ovError=e=>note(e.message||String(e),true,'overview');
function ovRemember(){
 clearTimeout(OV.saveTimer);OV.saveTimer=setTimeout(()=>{
  const data={mode:OV.mode,motion:OV.motion,files:OV.files,manual:OV.manual,positions:OV.positions,expanded:[...OV.expanded].slice(0,500),focus:OV.focus,selected:OV.selected,camera:OV.camera};
  OV.saveChain=(OV.saveChain||Promise.resolve()).catch(()=>{}).then(()=>api('overview-preferences',data)).catch(ovError);
 },350);
}
function ovAdd(node){if(node?.available)OV.nodes.set(node.path,node);}
async function ovChildren(path,more=false){
 const node=OV.nodes.get(path);if(!node)throw Error('目录已变化，请刷新');
 const offset=more?OV.next.get(path):0;if(more&&offset==null)return;
 const data=await api('overview-children?id='+encodeURIComponent(node.id)+'&files='+OV.files+'&offset='+offset);
 ovAdd(data.parent);data.nodes.forEach(ovAdd);
 OV.children.set(path,more?[...(OV.children.get(path)||[]),...data.nodes.map(n=>n.path)]:data.nodes.map(n=>n.path));
 OV.next.set(path,data.next_offset);
}
async function ovEnsure(path){
 if(!path)return OV.nodes.get('');
 const parts=path.split('/');let parent='';
 for(let i=0;i<parts.length;i++){
  const current=parts.slice(0,i+1).join('/');
  if(!OV.children.has(parent))await ovChildren(parent);
  while(!(OV.children.get(parent)||[]).includes(current)&&OV.next.get(parent)!=null)await ovChildren(parent,true);
  if(!(OV.children.get(parent)||[]).includes(current))throw Error('原位置已变化，或文件未显示，请刷新目录');
  OV.expanded.add(parent);parent=current;
 }
 return OV.nodes.get(path);
}
async function ovInit(){
 if(OV.initializing)return OV.initializing;
 OV.initializing=(async()=>{
  const data=await api('overview');ovAdd(data.root);OV.links=data.links;
  for(const link of OV.links){ovAdd(link.source);ovAdd(link.target);}
  if(!OV.ready){const p=data.preferences;for(const key of ['mode','motion','files','manual','positions','focus','selected','camera'])if(p[key]!==undefined)OV[key]=p[key];OV.restoreExpanded=p.expanded||[''];}
  await ovChildren('');
  if(!OV.ready){
   for(const path of OV.restoreExpanded.slice(0,80)){try{await ovEnsure(path);if(OV.nodes.get(path)?.kind==='directory'){OV.expanded.add(path);await ovChildren(path);}}catch{OV.expanded.delete(path);}}
   try{await ovEnsure(OV.focus);}catch{OV.focus='';}
   try{await ovEnsure(OV.selected);}catch{OV.selected='';}
   OV.ready=true;OV.needsFit=!data.preferences.camera;
  }
 })();try{await OV.initializing;}finally{OV.initializing=null;}
}
const ovPreviousRefresh=refreshPage;
refreshPage=async function(page=S.page){
 if(page!=='overview')return ovPreviousRefresh(page);
 await refreshStatus();await ovInit();if(S.page===page)render();
};
const ovPreviousRender=render;
render=function(){ovStop();if(S.page==='overview'){ovRender();syncTaskUI();return;}$('#content').classList.remove('overview-page');ovPreviousRender();};
function ovStop(){cancelAnimationFrame(OV.frame);OV.resize?.disconnect();OV.resize=null;}
function ovVisible(){
 const rows=[];let prioritize=false;
 function walk(path,depth=0){const node=OV.nodes.get(path);if(!node||rows.length>=240)return;if(node.kind==='file'&&!OV.files)return;rows.push({...node,depth});if(OV.expanded.has(path)){let children=OV.children.get(path)||[];if(prioritize)children=[...children].sort((a,b)=>Number(OV.selected===b||OV.selected.startsWith(b+'/'))-Number(OV.selected===a||OV.selected.startsWith(a+'/')));for(const child of children)walk(child,depth+1);}}
 walk(OV.focus);if(rows.length>=240&&!rows.some(n=>n.path===OV.selected)){prioritize=true;rows.length=0;walk(OV.focus);}return rows;
}
function ovRender(){
 const content=$('#content');content.classList.remove('list-page');content.classList.add('overview-page');
 if(!OV.ready){content.innerHTML=empty('正在读取目录','只读取顶层目录，不扫描正文。');return;}
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 content.innerHTML=`<div class="ov-toolbar"><div class="ov-switch" role="group" aria-label="总览视图"><button data-ov="mode" data-mode="graph" class="${OV.mode==='graph'?'active':''}">关系图</button><button data-ov="mode" data-mode="tree" class="${OV.mode==='tree'?'active':''}">目录树</button></div><form id="ov-search"><input id="ov-query" type="text" value="${esc(OV.query)}" placeholder="搜索文件夹或文件名称" aria-label="搜索目录总览"><select id="ov-scope" aria-label="搜索范围"><option value="all">目录与文件</option><option value="folders">仅目录</option></select><button class="primary" type="submit">搜索</button></form></div><div class="ov-tools"><label><input id="ov-files" type="checkbox" ${OV.files?'checked':''}>显示文件</label><label><input id="ov-manual" type="checkbox" ${OV.manual?'checked':''}>手动关联</label><label title="${reduced?'系统减少动态效果已启用':'幅度约3像素，操作时暂停'}"><input id="ov-motion" type="checkbox" ${OV.motion&&!reduced?'checked':''} ${reduced?'disabled':''}>漂浮动效</label><button data-ov="layout">自动排布</button><button data-ov="fit">适应画布</button><button data-ov="collapse">折叠分支</button><button data-ov="refresh">刷新目录</button><button data-ov="links">关联 ${OV.links.length}</button>${view('overview').job?'<button data-ov="stop-search">停止搜索</button>':''}<span class="ov-location" title="${esc(S.boot.status.root+'/'+OV.focus)}">${esc(OV.focus||'全库视图')}</span>${OV.focus?'<button data-ov="home">返回全库</button>':''}</div>${OV.linking!==undefined?`<div class="ov-linking">正在从「${esc(ovName(OV.linking))}」添加关联，请点击另一节点或搜索目标。<button data-ov="cancel-link">取消</button></div>`:''}<div class="ov-body">${OV.mode==='graph'?'<div id="ov-canvas" tabindex="0" aria-label="文件夹关系图，可拖动和缩放"><svg id="ov-svg"><g id="ov-lines"></g></svg><div id="ov-world"></div><div class="ov-canvas-tools"><button data-ov="zoom-out" aria-label="缩小">−</button><span id="ov-zoom">100%</span><button data-ov="zoom-in" aria-label="放大">＋</button><button data-ov="fit" aria-label="回到中心">⊡</button></div><div class="ov-legend"><span class="ov-solid"></span>目录层级 <span class="ov-dashed"></span>手动关联</div><svg id="ov-minimap" viewBox="0 0 200 125" aria-label="关系图小地图"></svg><div id="ov-info" class="ov-floating-info" hidden></div></div>':'<div class="ov-tree-wrap"><div id="ov-tree" role="tree" aria-label="资料目录树"></div><aside id="ov-tree-detail"></aside></div>'}<div id="ov-search-results" class="ov-results" hidden></div></div><div class="ov-footnote"><span>双击目录聚焦 · 拖动只调整视图 · 关联保存在本机</span><span id="ov-limit"></span></div>`;
 $('#ov-search').onsubmit=e=>{e.preventDefault();ovSearch().catch(ovError);};
 $('#ov-query').oninput=e=>OV.query=e.target.value;$('#ov-scope').value=OV.searchScope||'all';$('#ov-scope').onchange=e=>OV.searchScope=e.target.value;
 $('#ov-files').onchange=async e=>{OV.files=e.target.checked;OV.children.clear();OV.next.clear();try{await ovChildren('');for(const p of [...OV.expanded].filter(Boolean)){await ovEnsure(p);await ovChildren(p);}ovRemember();if(S.page==='overview')render();}catch(error){ovError(error);}};
 $('#ov-manual').onchange=e=>{OV.manual=e.target.checked;ovRemember();render();};
 $('#ov-motion').onchange=e=>{OV.motion=e.target.checked;ovRemember();render();};
 if(OV.mode==='graph')ovGraph();else ovTree();
 if(OV.showDetail)ovDetails();ovResults();
}
async function ovExpand(path,more=false){
 const node=OV.nodes.get(path);if(node?.kind!=='directory')return;
 if(!more&&OV.expanded.has(path)){OV.expanded.delete(path);}else{await ovChildren(path,more);OV.expanded.add(path);}
 ovRemember();if(S.page==='overview')render();
}
async function ovFocus(path){
 const node=await ovEnsure(path);if(node.kind!=='directory')return ovLocate(node);
 OV.focus=path;OV.selected=path;OV.expanded.add(path);if(!OV.children.has(path))await ovChildren(path);
 OV.needsFit=true;OV.showDetail=false;ovRemember();if(S.page==='overview')render();
}
async function ovSelect(path){
 const node=OV.nodes.get(path);if(!node)return;OV.detailError=null;
 if(OV.linking!==undefined&&path!==OV.linking){const first=OV.nodes.get(OV.linking);OV.linking=undefined;ovLinkDialog(first,node);return;}
 OV.selected=path;OV.showDetail=true;OV.selectedLink=null;ovRemember();
 $$('.ov-node').forEach(e=>e.classList.toggle('selected',e.dataset.path===path));$$('.ov-tree-row').forEach(e=>e.classList.toggle('selected',e.dataset.path===path));
 ovDetails();ovDraw();
 const seq=OV.detailSeq=(OV.detailSeq||0)+1;
 try{const fresh=await api('overview-detail?id='+encodeURIComponent(node.id));if(seq===OV.detailSeq){ovAdd(fresh);if(S.page==='overview'&&OV.selected===path)ovDetails();}}
 catch(error){if(seq===OV.detailSeq){OV.detailError=error.message;ovDetails();}}
}
function ovDetails(){
 if(S.page!=='overview'||!OV.showDetail||OV.linking!==undefined)return;const node=OV.nodes.get(OV.selected);if(!node)return;
 const area=OV.mode==='graph'?$('#ov-info'):$('#ov-tree-detail');if(!area)return;area.hidden=false;
 const related=OV.links.filter(l=>l.source.path===node.path||l.target.path===node.path);
 area.innerHTML=`<div class="ov-detail-head"><strong>${node.kind==='directory'?'目录详情':'文件详情'}</strong><button data-ov="close-detail" aria-label="关闭详情">×</button></div><div class="ov-detail-name">${ovFolderIcon(node.kind)}<strong>${esc(node.name)}</strong></div><div class="ov-meta"><label>相对位置</label><div>${esc(node.path||'资料库根目录')}</div><label>类型</label><div>${node.kind==='directory'?'文件夹':esc(node.name.split('.').pop().toUpperCase())}${node.managed?' · 管理目录':''}</div>${node.kind==='file'?`<label>大小</label><div>${(node.size/1024).toLocaleString(undefined,{maximumFractionDigits:1})} KB</div>`:''}${node.directories!==undefined?`<label>直接内容</label><div>${node.directories} 个子目录 · ${node.files} 个文件${node.count_limited?'（已计数部分）':''}</div>`:''}<label>修改时间</label><div>${new Date(node.modified*1000).toLocaleString()}</div></div>${OV.detailError?`<p class="hint">${esc(OV.detailError)}</p>`:''}<div class="ov-detail-actions"><button data-ov="open" data-path="${esc(node.path)}">${node.kind==='directory'?'打开目录':'显示所在目录'}</button><button data-ov="copy" data-path="${esc(node.path)}">复制路径</button><button data-ov="add-link" data-path="${esc(node.path)}">添加关联</button>${node.kind==='directory'?`<button data-ov="focus" data-path="${esc(node.path)}">聚焦此目录</button>`:''}</div>${related.length?`<div class="ov-related"><strong>手动关联</strong>${related.map(link=>{const other=link.source.path===node.path?link.target:link.source;return `<div><span>${esc(link.label)} → ${esc(other.name)}</span>${!other.available?'<span class="badge amber">需重新定位</span>':`<button data-ov="locate" data-path="${esc(other.path)}">定位</button>`}<button data-ov="edit-link" data-id="${esc(link.id)}">编辑</button></div>`;}).join('')}</div>`:''}`;
}
function ovFolderIcon(kind){return kind==='directory'?'<span class="ov-folder-icon"></span>':'<span class="ov-file-icon">▤</span>';}
function ovTree(){
 const area=$('#ov-tree'),rows=ovVisible();area.innerHTML=rows.map(n=>`<div class="ov-tree-row ${n.path===OV.selected?'selected':''}" data-path="${esc(n.path)}" role="treeitem" aria-level="${n.depth+1}" aria-expanded="${OV.expanded.has(n.path)}" data-depth="${n.depth}"><button class="ov-toggle" data-ov="expand" data-path="${esc(n.path)}" ${n.kind==='file'?'disabled':''} aria-label="展开 ${esc(n.name)}">${n.kind==='directory'?(OV.expanded.has(n.path)?'⌄':'›'):''}</button>${ovFolderIcon(n.kind)}<span>${esc(n.name)}</span>${n.managed?'<span class="badge">管理</span>':''}<button data-ov="focus" data-path="${esc(n.path)}" class="ov-tree-focus" title="聚焦目录">⊡</button></div>${OV.expanded.has(n.path)&&OV.next.get(n.path)!=null?`<button class="ov-more" data-ov="more" data-path="${esc(n.path)}">加载「${esc(n.name)}」的更多内容</button>`:''}`).join('');
 area.querySelectorAll('.ov-tree-row').forEach(row=>{row.style.paddingLeft=(14+Number(row.dataset.depth)*23)+'px';row.onclick=e=>{if(!e.target.closest('button'))ovSelect(row.dataset.path).catch(ovError);};row.ondblclick=e=>{if(!e.target.closest('button'))ovFocus(row.dataset.path).catch(ovError);};});
 area.scrollTop=OV.treeScroll||0;area.onscroll=()=>OV.treeScroll=area.scrollTop;
 $('#ov-tree-detail').innerHTML=empty('选择目录或文件','查看路径、资料信息与手动关联。');
 if(rows.length>=240)$('#ov-limit').textContent='当前显示240个节点，可聚焦分支继续浏览';
}
function ovLayout(rows){
 const visible=new Set(rows.map(n=>n.path));const byPath=new Map(rows.map(n=>[n.path,n]));const positions=new Map();
 const kids=p=>(OV.expanded.has(p)?OV.children.get(p)||[]:[]).filter(p=>visible.has(p));
 const weight=p=>Math.max(1,kids(p).reduce((n,k)=>n+weight(k),0));
 function place(p,x,y,side,color){positions.set(p,{x,y,color,node:byPath.get(p)});const list=kids(p);let cursor=y-weight(p)*49;for(const k of list){const h=weight(k)*98;place(k,x+side*285,cursor+h/2,side,color);cursor+=h;}}
 const root=OV.focus;positions.set(root,{x:0,y:0,color:ovColors[0],node:byPath.get(root)});const list=kids(root);
 const left=OV.focus?[]:list.slice(0,Math.ceil(list.length/2)),right=OV.focus?list:list.slice(left.length);
 for(const [group,side] of [[left,-1],[right,1]]){let cursor=-group.reduce((n,p)=>n+weight(p)*98,0)/2;group.forEach((p,i)=>{const h=weight(p)*98;place(p,side*310,cursor+h/2,side,ovColors[(list.indexOf(p))%ovColors.length]);cursor+=h;});}
 for(const [path,pos] of positions){pos.baseX=pos.x;pos.baseY=pos.y;pos.x+=OV.positions[path]?.x||0;pos.y+=OV.positions[path]?.y||0;pos.dx=0;pos.dy=0;}
 return positions;
}
function ovGraph(){
 const canvas=$('#ov-canvas'),world=$('#ov-world');OV.graph=ovLayout(ovVisible());OV.cardEls=new Map();
 for(const [path,p] of OV.graph){
  const n=p.node,card=document.createElement('div');card.className='ov-node'+(path===OV.selected?' selected':'')+(path===OV.focus?' root-node':'');card.dataset.path=path;card.setAttribute('role','button');card.tabIndex=0;card.setAttribute('aria-label',n.name);card.style.left=(p.x-108)+'px';card.style.top=(p.y-33)+'px';card.style.setProperty('--branch',p.color);
  card.innerHTML=`${ovFolderIcon(n.kind)}<span class="ov-node-label" title="${esc(n.path||n.name)}">${esc(n.name)}</span>${n.kind==='directory'?`<button data-ov="expand" data-path="${esc(path)}" aria-label="展开 ${esc(n.name)}">${OV.expanded.has(path)?'−':'＋'}</button>`:''}`;
  card.onpointerenter=()=>OV.hover=path;card.onpointerleave=()=>{if(OV.hover===path)OV.hover=null;};
  card.onpointerdown=e=>ovDragNode(e,path,card);card.ondblclick=e=>{if(!e.target.closest('button'))ovFocus(path).catch(ovError);};card.onkeydown=e=>{if(e.key==='Enter')ovSelect(path).catch(ovError);};
  world.append(card);OV.cardEls.set(path,card);
 }
 OV.hierarchy=[];for(const [path,p] of OV.graph){const parent=ovParent(path);if(path!==OV.focus&&OV.graph.has(parent))OV.hierarchy.push({source:parent,target:path,color:p.color});}
 const lineArea=$('#ov-lines');lineArea.innerHTML=OV.hierarchy.map((l,i)=>`<path class="ov-parent-edge" data-edge="${i}" stroke="${l.color}"/>`).join('')+(OV.manual?OV.links.filter(l=>l.source.available&&l.target.available&&OV.graph.has(l.source.path)&&OV.graph.has(l.target.path)).map(l=>`<g class="ov-manual-edge" data-link="${esc(l.id)}"><path class="ov-link-hit"/><path class="ov-link-line"/><g class="ov-link-label"><rect x="-67" y="-13" width="134" height="26" rx="13"/><text text-anchor="middle" y="4">${esc(l.label.length>12?l.label.slice(0,12)+'…':l.label)}</text></g></g>`).join(''):'');
 lineArea.querySelectorAll('[data-link]').forEach(e=>{e.onclick=event=>{event.stopPropagation();ovLinkInspector(e.dataset.link);};});
 canvas.onwheel=ovWheel;canvas.onpointerdown=ovPan;canvas.onkeydown=e=>{if(e.key==='Escape'){OV.linking=undefined;OV.showDetail=false;render();}if(e.key==='0'){OV.needsFit=true;ovFit();}if(e.key==='+'||e.key==='=')ovZoom(1.15);if(e.key==='-')ovZoom(1/1.15);};
 OV.resize=new ResizeObserver(()=>{if(OV.needsFit)ovFit();else ovDraw();});OV.resize.observe(canvas);
 if(OV.needsFit)ovFit();else ovDraw();ovMinimap();ovAnimate();
 if(OV.graph.size>=240)$('#ov-limit').textContent='已显示240个节点，请聚焦分支继续';
 const more=[...OV.graph.keys()].filter(p=>OV.expanded.has(p)&&OV.next.get(p)!=null);if(more.length)$('#ov-limit').innerHTML=`<button data-ov="more" data-path="${esc(more[0])}">更多：${esc(ovName(more[0]))}</button>`;
}
function ovCurve(a,b){const sx=a.x+a.dx+(b.x>=a.x?108:-108),sy=a.y+a.dy,tx=b.x+b.dx+(b.x>=a.x?-108:108),ty=b.y+b.dy;const bend=Math.max(45,Math.abs(tx-sx)*.48)*(tx>=sx?1:-1);return {d:`M${sx},${sy} C${sx+bend},${sy} ${tx-bend},${ty} ${tx},${ty}`,x:(sx+tx)/2,y:(sy+ty)/2};}
function ovDraw(){
 if(S.page!=='overview'||OV.mode!=='graph'||!$('#ov-world'))return;
 const c=OV.camera;$('#ov-world').style.transform=`translate(${c.x}px,${c.y}px) scale(${c.zoom})`;$('#ov-lines').setAttribute('transform',`translate(${c.x} ${c.y}) scale(${c.zoom})`);$('#ov-zoom').textContent=Math.round(c.zoom*100)+'%';
 for(const [path,p] of OV.graph)OV.cardEls.get(path).style.transform=`translate(${p.dx}px,${p.dy}px)`;
 for(const [i,l] of OV.hierarchy.entries())$('#ov-lines [data-edge="'+i+'"]').setAttribute('d',ovCurve(OV.graph.get(l.source),OV.graph.get(l.target)).d);
 for(const element of $('#ov-lines').querySelectorAll('[data-link]')){const l=OV.links.find(l=>l.id===element.dataset.link);const curve=ovCurve(OV.graph.get(l.source.path),OV.graph.get(l.target.path));element.querySelectorAll('path').forEach(p=>p.setAttribute('d',curve.d));element.querySelector('.ov-link-label').setAttribute('transform',`translate(${curve.x} ${curve.y})`);element.classList.toggle('highlight',OV.selected===l.source.path||OV.selected===l.target.path);}
}
function ovAnimate(){
 cancelAnimationFrame(OV.frame);
 if(!OV.motion||document.hidden||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
 function frame(t){
  if(S.page!=='overview'||OV.mode!=='graph'||document.hidden)return;
  if(!OV.drag&&!$('#modal').open)for(const [path,p] of OV.graph){if((path===OV.selected&&OV.showDetail)||path===OV.hover)continue;const seed=[...path].reduce((n,c)=>n+c.charCodeAt(0),0);p.dx=Math.sin(t/(1450+seed%650)+seed)*2.2;p.dy=Math.sin(t/(1750+seed%700)+seed*.7)*3;}
  ovDraw();OV.frame=requestAnimationFrame(frame);
 }
 OV.frame=requestAnimationFrame(frame);
}
function ovFit(){const area=$('#ov-canvas');if(!area||!OV.graph?.size)return;const points=[...OV.graph.values()];const minX=Math.min(...points.map(p=>p.x))-130,maxX=Math.max(...points.map(p=>p.x))+130,minY=Math.min(...points.map(p=>p.y))-65,maxY=Math.max(...points.map(p=>p.y))+65;const zoom=Math.max(.2,Math.min(1.1,(area.clientWidth-55)/(maxX-minX),(area.clientHeight-90)/(maxY-minY)));OV.camera={x:area.clientWidth/2-(minX+maxX)/2*zoom,y:area.clientHeight/2-(minY+maxY)/2*zoom-10,zoom};OV.needsFit=false;ovDraw();ovRemember();}
function ovZoom(factor,x,y){const area=$('#ov-canvas');if(!area)return;const c=OV.camera,zoom=Math.max(.2,Math.min(2.5,c.zoom*factor));x??=area.clientWidth/2;y??=area.clientHeight/2;OV.camera={x:x-(x-c.x)*zoom/c.zoom,y:y-(y-c.y)*zoom/c.zoom,zoom};ovDraw();ovRemember();}
function ovWheel(e){if(e.target.closest('.ov-floating-info'))return;e.preventDefault();const rect=e.currentTarget.getBoundingClientRect();ovZoom(Math.exp(-e.deltaY*.001),e.clientX-rect.left,e.clientY-rect.top);}
function ovPan(e){if(e.button!==0||e.target.closest('.ov-node,button,.ov-floating-info,.ov-manual-edge,#ov-minimap'))return;const canvas=e.currentTarget,start={};OV.drag=true;canvas.setPointerCapture(e.pointerId);start.cameraX=OV.camera.x;start.cameraY=OV.camera.y;start.clientX=e.clientX;start.clientY=e.clientY;canvas.onpointermove=ev=>{OV.camera.x=start.cameraX+ev.clientX-start.clientX;OV.camera.y=start.cameraY+ev.clientY-start.clientY;ovDraw();};canvas.onpointerup=()=>{OV.drag=false;canvas.onpointermove=null;ovRemember();};canvas.onpointercancel=canvas.onpointerup;}
function ovDragNode(e,path,card){
 if(e.button!==0||e.target.closest('button'))return;e.stopPropagation();card.setPointerCapture(e.pointerId);const start={x:e.clientX,y:e.clientY,offset:{...(OV.positions[path]||{x:0,y:0})}};let moved=false;OV.drag=true;
 card.onpointermove=ev=>{if(Math.hypot(ev.clientX-start.x,ev.clientY-start.y)>4)moved=true;if(!moved)return;OV.positions[path]={x:start.offset.x+(ev.clientX-start.x)/OV.camera.zoom,y:start.offset.y+(ev.clientY-start.y)/OV.camera.zoom};const p=OV.graph.get(path);p.x=p.baseX+OV.positions[path].x;p.y=p.baseY+OV.positions[path].y;card.style.left=(p.x-108)+'px';card.style.top=(p.y-33)+'px';ovDraw();};
 card.onpointerup=()=>{OV.drag=false;card.onpointermove=null;card.onpointerup=null;if(moved){ovRemember();ovMinimap();}else ovSelect(path).catch(ovError);};card.onpointercancel=()=>{OV.drag=false;card.onpointermove=null;};
}
function ovMinimap(){const svg=$('#ov-minimap');if(!svg||!OV.graph?.size)return;const points=[...OV.graph.values()],xs=points.map(p=>p.x),ys=points.map(p=>p.y);const minX=Math.min(...xs)-120,minY=Math.min(...ys)-50,w=Math.max(...xs)-minX+120,h=Math.max(...ys)-minY+50;svg.setAttribute('viewBox',`${minX} ${minY} ${w} ${h}`);svg.innerHTML=OV.hierarchy.map(l=>`<path fill="none" stroke="#c1d5ee" stroke-width="8" d="${ovCurve(OV.graph.get(l.source),OV.graph.get(l.target)).d}"/>`).join('')+points.map(p=>`<rect x="${p.x-100}" y="${p.y-23}" width="200" height="46" rx="10" fill="${p.color}" opacity=".65"/>`).join('');svg.onclick=()=>ovFit();}
async function ovSearch(){
 const query=$('#ov-query').value.trim();if(!query)throw Error('请输入要搜索的名称或路径');OV.query=query;const files=$('#ov-scope').value==='all',seq=++OV.searchSeq,v=view('overview');
 if(v.job?.id)await api('overview-stop',{id:v.job.id});const task={action:'overview-search',phase:'正在搜索名称与路径…'};v.job=task;v.taskFailed=false;if(S.page==='overview')render();syncTaskUI();
 try{const started=await api('overview-search',{query,files});task.id=started.job_id;while(seq===OV.searchSeq){const j=await api('jobs/'+task.id);task.phase=j.phase;syncTaskUI();if(j.state==='error')throw Error(j.error);if(j.state==='done'){OV.results=j.result;j.result.nodes.forEach(ovAdd);if(S.page==='overview')ovResults();break;}await new Promise(r=>setTimeout(r,200));}}
 catch(error){if(seq===OV.searchSeq){v.taskFailed=true;ovError(error);}}
 finally{if(v.job===task){v.job=null;if(S.page!=='overview')v.unread++;if(S.page==='overview')render();syncTaskUI();}view('history').dirty=true;}
}
function ovResults(){const area=$('#ov-search-results');if(!area)return;area.hidden=!OV.results;if(!OV.results)return;const r=OV.results;area.innerHTML=`<div class="ov-detail-head"><strong>搜索结果 ${r.nodes.length}</strong><button data-ov="close-results" aria-label="关闭搜索结果">×</button></div><p class="hint">${r.stopped?'搜索已停止 · ':''}${r.limited?'已达到本次上限，可细化关键词 · ':''}已读 ${r.scanned} 项${r.errors?' · '+r.errors+' 处不可读取':''}</p>${r.nodes.length?r.nodes.map(n=>`<button class="ov-result" data-ov="locate" data-path="${esc(n.path)}">${ovFolderIcon(n.kind)}<span><strong>${esc(n.name)}</strong><small>${esc(n.path)}</small></span></button>`).join(''):empty('没有匹配项','换一个名称或相对路径试试。')}<button data-ov="stop-search" ${view('overview').job?'':'hidden'}>停止搜索</button>`;}
async function ovLocate(nodeOrPath){const path=typeof nodeOrPath==='string'?nodeOrPath:nodeOrPath.path;const known=OV.nodes.get(path);if(known?.kind==='file'){OV.files=true;await ovEnsure(ovParent(path));await ovChildren(ovParent(path));}await ovEnsure(path);OV.focus='';OV.results=null;OV.needsFit=false;OV.showDetail=true;OV.selected=path;ovRemember();if(S.page==='overview'){render();if(OV.mode==='graph'){const point=OV.graph.get(path),area=$('#ov-canvas');if(point){OV.camera={x:area.clientWidth/2-point.x,y:area.clientHeight/2-point.y,zoom:1};ovDraw();}}else{[...$('#ov-tree').children].find(e=>e.dataset.path===path)?.scrollIntoView({block:'center'});}await ovSelect(path);}}
function ovLinkDialog(source,target,existing=null){
 const options=[...OV.nodes.values()].filter(n=>n.available);const optionList=selected=>options.map(n=>`<option value="${esc(n.id)}" ${n.path===selected?.path?'selected':''}>${esc(n.path||n.name)}</option>`).join('');
 OV.editingLink=existing?.id||null;
 modal(existing?'编辑手动关联':'添加手动关联',`<p>虚线只记录关联，不改变目录结构。未列出的节点可先在图中展开或搜索。</p><div class="field"><label for="ov-link-source">起点</label><select id="ov-link-source" class="full-width"><option value="">请选择起点</option>${optionList(source)}</select></div><div class="field"><label for="ov-link-target">终点</label><select id="ov-link-target" class="full-width"><option value="">请选择终点</option>${optionList(target)}</select></div><div class="field"><label for="ov-link-label">关系说明</label><input id="ov-link-label" class="full-width" maxlength="60" value="${esc(existing?.label||'参考资料')}"></div><div class="field"><label for="ov-link-note">备注（可选）</label><textarea id="ov-link-note" maxlength="500">${esc(existing?.note||'')}</textarea></div><div class="modal-actions"><button data-action="close-modal">返回</button><button class="primary" data-ov="save-link">保存关联</button></div>`,'overview');
}
function ovLinkInspector(id){const link=OV.links.find(l=>l.id===id);if(!link)return;OV.showDetail=true;const area=OV.mode==='graph'?$('#ov-info'):$('#ov-tree-detail');if(!area)return;area.hidden=false;area.innerHTML=`<div class="ov-detail-head"><strong>手动关联</strong><button data-ov="close-detail">×</button></div><p class="ov-link-pair">${esc(link.source.name)} → ${esc(link.target.name)}</p><div class="ov-meta"><label>关系说明</label><div>${esc(link.label)}</div><label>两端位置</label><div>${esc(link.source.path)}<br>↓<br>${esc(link.target.path)}</div>${link.note?`<label>备注</label><div>${esc(link.note)}</div>`:''}</div>${!link.source.available||!link.target.available?'<p class="hint">关联端点需重新定位，请编辑并选择新的节点。</p>':''}<div class="ov-detail-actions"><button data-ov="edit-link" data-id="${id}">编辑关联</button><button data-ov="remove-link" data-id="${id}" class="danger">移除关联</button></div><p class="hint">仅移除连线，不影响资料。</p>`;}
async function ovReloadLinks(){const d=await api('overview');OV.links=d.links;for(const l of OV.links){ovAdd(l.source);ovAdd(l.target);}}
async function ovAction(action,button){
 const path=button.dataset.path;
 if(action==='mode'){OV.mode=button.dataset.mode;ovRemember();render();}
 if(action==='expand')await ovExpand(path);
 if(action==='more')await ovExpand(path,true);
 if(action==='focus')await ovFocus(path);
 if(action==='home')await ovFocus('');
 if(action==='fit'){OV.needsFit=true;ovFit();}
 if(action==='layout'){OV.positions={};OV.needsFit=true;ovRemember();render();}
 if(action==='collapse'){OV.expanded=new Set([OV.focus]);OV.needsFit=true;ovRemember();render();}
 if(action==='refresh'){const focus=OV.nodes.get(OV.selected)?.kind==='directory'?OV.selected:ovParent(OV.selected);try{await ovChildren(focus);}catch{const parent=ovParent(focus);await ovChildren(parent);OV.selected=parent;OV.focus='';OV.needsFit=true;note('原位置已变化，已刷新父目录；关联仍需手动核对。',false,'overview');}await ovReloadLinks();OV.detailError=null;render();}
 if(action==='zoom-in')ovZoom(1.15);if(action==='zoom-out')ovZoom(1/1.15);
 if(action==='open')await api('overview-open',{id:OV.nodes.get(path).id});
 if(action==='copy'){await navigator.clipboard.writeText(OV.nodes.get(path).absolute_path);note('完整路径已复制。',false,'overview');}
 if(action==='close-detail'){OV.showDetail=false;const area=OV.mode==='graph'?$('#ov-info'):$('#ov-tree-detail');if(area)area.hidden=true;}
 if(action==='locate')await ovLocate(path);
 if(action==='close-results'){OV.results=null;ovResults();}
 if(action==='stop-search'&&view('overview').job?.id)await api('overview-stop',{id:view('overview').job.id});
 if(action==='add-link'){OV.linking=path;OV.showDetail=false;render();}
 if(action==='cancel-link'){OV.linking=undefined;render();}
 if(action==='edit-link'){const link=OV.links.find(l=>l.id===button.dataset.id);ovLinkDialog(link.source,link.target,link);}
 if(action==='save-link'){
  const chosenSource=$('#ov-link-source').value,chosenTarget=$('#ov-link-target').value;const endpoints=[...OV.nodes.values()].filter(n=>[chosenSource,chosenTarget].includes(n.id));await api('overview-link',{source_id:$('#ov-link-source').value,target_id:$('#ov-link-target').value,label:$('#ov-link-label').value,note:$('#ov-link-note').value,link_id:OV.editingLink});closeModal('overview');await ovReloadLinks();OV.manual=true;OV.focus='';for(const endpoint of endpoints){if(endpoint.kind==='file')OV.files=true;await ovEnsure(ovParent(endpoint.path));await ovChildren(ovParent(endpoint.path));await ovEnsure(endpoint.path);}OV.needsFit=true;ovRemember();render();note('手动关联已保存，未移动原资料。',false,'overview');
 }
 if(action==='remove-link'){await api('overview-unlink',{id:button.dataset.id});await ovReloadLinks();closeModal('overview');render();note('关联已移除，原资料保留。',false,'overview');}
 if(action==='links'){modal('手动关联',OV.links.length?OV.links.map(l=>`<div class="preview-item"><strong>${esc(l.label)}</strong><p>${esc(l.source.path)} → ${esc(l.target.path)}</p>${!l.source.available||!l.target.available?'<span class="badge amber">端点需重新定位</span>':''}<button data-ov="edit-link" data-id="${l.id}">编辑或重新绑定</button><button data-ov="remove-link" data-id="${l.id}">移除关联</button></div>`).join(''):empty('还没有手动关联','选中卡片后点击“添加关联”。'),'overview');}
}
document.addEventListener('click',e=>{const b=e.target.closest('[data-ov]');if(!b||b.disabled)return;e.preventDefault();e.stopPropagation();b.disabled=true;ovAction(b.dataset.ov,b).catch(ovError).finally(()=>{if(b.isConnected)b.disabled=false;});});
document.addEventListener('visibilitychange',()=>{if(document.hidden)cancelAnimationFrame(OV.frame);else if(S.page==='overview'&&OV.mode==='graph')ovAnimate();});
matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',()=>{if(S.page==='overview')render();});
