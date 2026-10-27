// Real browser interactions against isolated synthetic data; no original files or live AI.
const {chromium}=require('playwright'),{spawn}=require('child_process'),readline=require('readline');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..');
const child=spawn(process.env.PYTHON||'python',['-X','utf8',path.join(__dirname,'web_fixture.py'),'--overview-fixture','--workspace-fixture'],{cwd:root,windowsHide:true});
let fixture,browser,page;
async function main(){
 fixture=await new Promise((resolve,reject)=>{readline.createInterface({input:child.stdout}).once('line',s=>{try{resolve(JSON.parse(s));}catch(e){reject(e);}});child.stderr.on('data',d=>process.stderr.write(d));child.once('error',reject);});
 browser=await chromium.launch({headless:true,channel:'msedge'});page=await browser.newPage({viewport:{width:1600,height:1050},reducedMotion:'no-preference'});
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 const card=p=>page.locator('.ov-node[data-path="'+p+'"]');
 const nav=async p=>{await page.locator('nav [data-page='+p+']').click();};
 const expand=async p=>{await page.getByRole('button',{name:'展开 '+p.split('/').pop(),exact:true}).click({force:true});await page.waitForFunction(p=>OV.expanded.has(p)&&OV.children.has(p),p);await page.getByRole('button',{name:'适应画布',exact:true}).click();};
 await page.goto(fixture.url);await page.waitForFunction(()=>!!S.views.inbox?.job);
 await nav('overview');await card('02_项目').waitFor();
 assert((await page.locator('.ov-node').count())>=12);assert((await page.locator('.ov-node').count())<30);
 // The tested packaged assets use JS animation: inspect actual live transforms.
 const drifting=card('01_课程学习');const t1=await drifting.evaluate(e=>e.style.transform);await page.waitForTimeout(220);const t2=await drifting.evaluate(e=>e.style.transform);assert.notEqual(t1,t2);
 await drifting.hover({force:true});await page.waitForTimeout(80);const frozen=await drifting.evaluate(e=>e.style.transform);await page.waitForTimeout(200);assert.equal(await drifting.evaluate(e=>e.style.transform),frozen);
 const project=card('02_项目'),box=await project.boundingBox();await page.mouse.move(box.x+75,box.y+30);await page.mouse.down();await page.mouse.move(box.x+115,box.y+50,{steps:8});await page.mouse.up();
 assert((await page.evaluate(()=>OV.positions['02_项目'].x))>25);
 await expand('02_项目');await card('02_项目/智能小车').waitFor();
 await expand('06_资料库');await expand('06_资料库/学科资料');await expand('06_资料库/学科资料/控制理论');
 await card('02_项目/智能小车').click({force:true});await page.locator('#ov-info [data-ov=add-link]').click();await card('06_资料库/学科资料/控制理论').click({force:true});
 await page.locator('#ov-link-label').fill('参考资料<img src=x onerror=window.injected=1>');await page.locator('#ov-link-note').fill('仅记录关系，不改变目录');await page.getByRole('button',{name:'保存关联',exact:true}).click();
 await page.locator('dialog').waitFor({state:'hidden'});await page.waitForFunction(()=>OV.links.length===1);await page.locator('.ov-manual-edge').waitFor();
 assert.equal(await page.evaluate(()=>window.injected),undefined);assert.equal(await page.locator('.ov-link-label img').count(),0);
 await page.getByRole('button',{name:'目录树',exact:true}).click();await page.locator('.ov-tree-row[data-path="02_项目/智能小车"]').waitFor();
 assert((await page.locator('.ov-tree-row[data-path="02_项目/智能小车"]').evaluate(e=>parseFloat(e.style.paddingLeft)))>30);
 await page.getByRole('button',{name:'关系图',exact:true}).click();await page.locator('.ov-manual-edge').waitFor();
 // Edit via relationship list; the note remains literal text.
 await page.locator('[data-ov=links]').click();await page.getByRole('button',{name:'编辑或重新绑定',exact:true}).click();await page.locator('#ov-link-label').fill('参考资料');await page.getByRole('button',{name:'保存关联',exact:true}).click();await page.locator('dialog').waitFor({state:'hidden'});
 await page.locator('#ov-query').fill('控制理论笔记');await page.locator('#ov-search button[type=submit]').click();await page.waitForFunction(()=>!!S.views.overview.job);
 await nav('settings');await page.waitForFunction(()=>!S.views.overview.job&&S.views.overview.unread>0);assert.equal(await page.evaluate(()=>S.page),'settings');
 assert(await page.locator('nav [data-page=overview] .nav-task').isVisible());
 await nav('overview');await page.locator('.ov-result').first().waitFor();await page.locator('.ov-result').first().click();await card('06_资料库/学科资料/控制理论/控制理论笔记.md').waitFor();
 assert.equal(await page.evaluate(()=>OV.files),true);assert.equal(await page.evaluate(()=>OV.selected),'06_资料库/学科资料/控制理论/控制理论笔记.md');
 await page.locator('#ov-motion').uncheck();const still=await card('03_竞赛').evaluate(e=>e.style.transform);await page.waitForTimeout(180);assert.equal(await card('03_竞赛').evaluate(e=>e.style.transform),still);
 await page.emulateMedia({reducedMotion:'reduce'});assert(await page.locator('#ov-motion').isDisabled());await page.emulateMedia({reducedMotion:'no-preference'});
 await page.getByRole('button',{name:'目录树',exact:true}).click();await page.waitForTimeout(700);await page.evaluate(()=>OV.saveChain);
 await page.reload();await nav('overview');await page.locator('#ov-tree').waitFor();assert.equal(await page.evaluate(()=>OV.mode),'tree');assert.equal(await page.evaluate(()=>OV.motion),false);assert.equal(await page.evaluate(()=>OV.links.length),1);
 await page.getByRole('button',{name:'关系图',exact:true}).click();await page.locator('.ov-manual-edge').waitFor();
 // Endpoint replacement must never silently rebind an existing manual relation.
 const original=path.join(fixture.root,'02_项目/智能小车');fs.renameSync(original,original+'-old');fs.mkdirSync(original);
 await page.locator('#refresh').click();await page.waitForFunction(()=>OV.links.some(l=>!l.source.available));
 await page.locator('[data-ov=links]').click();assert(await page.getByText('端点需重新定位',{exact:true}).isVisible());await page.getByRole('button',{name:'移除关联',exact:true}).click();await page.waitForFunction(()=>OV.links.length===0);
 assert(fs.existsSync(original));assert(fs.existsSync(original+'-old'));assert.equal(fs.readFileSync(path.join(fixture.root,'06_资料库/学科资料/控制理论/控制理论笔记.md'),'utf8'),'synthetic control notes');
 if(await page.locator('dialog').isVisible())await page.locator('#modal-close').click();await page.setViewportSize({width:950,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);
 if(process.env.OVERVIEW_SCREENSHOT)await page.screenshot({path:process.env.OVERVIEW_SCREENSHOT});
 console.log('PASS: independent lazy navigation, floating/pause/reduced motion, dragging, tree/graph switching, manual link CRUD and stale endpoints, literal text, search/navigation badges, persistence and original-file preservation.');
}
main().catch(async e=>{console.error(e);if(page){console.error(await page.locator('#notice').textContent());console.error(await page.locator('dialog').textContent());if(process.env.OVERVIEW_SCREENSHOT)await page.screenshot({path:process.env.OVERVIEW_SCREENSHOT});}process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(fixture)fs.writeFileSync(fixture.stop,'stop');else child.kill();});
