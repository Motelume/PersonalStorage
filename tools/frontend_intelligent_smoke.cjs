// End-to-end synthetic inbox/AI/audit test; no real documents or live model calls.
const {chromium}=require('playwright');const {spawn}=require('child_process');const readline=require('readline');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..');const child=spawn(process.env.PYTHON||'python',['-X','utf8',path.join(__dirname,'web_fixture.py'),'--intelligent-fixture'],{cwd:root,windowsHide:true});
let fixture,browser,page;
async function main(){
 fixture=await new Promise((resolve,reject)=>{readline.createInterface({input:child.stdout}).once('line',s=>{try{resolve(JSON.parse(s));}catch(e){reject(e);}});child.stderr.on('data',d=>process.stderr.write(d));child.once('error',reject);});
 browser=await chromium.launch({headless:true,channel:'msedge'});page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 const idle=()=>page.waitForFunction(()=>!S.busy&&!S.acting);
 const nav=async name=>{await page.locator(`nav [data-page=${name}]`).click();await idle();};
 await page.goto(fixture.url);await page.locator('tbody tr').filter({hasText:'unknown.txt'}).waitFor();await idle();
 assert.equal(await page.locator('nav button').count(),7);
 await nav('inboxes');await page.locator('#inbox-path').waitFor();
 await page.locator('#inbox-label').fill('合成下载来源');await page.locator('#inbox-path').fill(fixture.downloads);
 await page.getByRole('button',{name:'添加收件箱',exact:true}).click();await idle();
 await page.getByText('合成下载来源',{exact:true}).waitFor();
 await nav('inbox');await page.locator('tbody tr').filter({hasText:'download.txt'}).waitFor();await idle();
 await page.getByRole('button',{name:'扫描并智能分类',exact:false}).click();await idle();
 assert.equal(await page.evaluate(()=>S.rows.find(r=>r.source.endsWith('unknown.txt')).decision),'move');
 await page.getByRole('checkbox',{name:'选择 download.txt',exact:true}).check();
 await page.getByRole('button',{name:'预览已选计划',exact:false}).click();await page.getByRole('button',{name:'执行这 1 项',exact:true}).waitFor();
 await page.getByRole('button',{name:'执行这 1 项',exact:true}).click();await idle();
 assert(fs.existsSync(path.join(fixture.downloads,'download.txt')));assert(fs.existsSync(path.join(fixture.root,'01_课程学习/测试课程/download.txt')));
 assert.equal(await page.locator('tbody tr').filter({hasText:'download.txt'}).count(),0);
 // Change an already imported source to move, reuse the verified archive,
 // force a sharing violation, and explicitly retry cleanup through history.
 await nav('inboxes');const sourceCard=page.locator('.inbox-source').filter({hasText:'合成下载来源'});
 await sourceCard.locator('[data-source-mode]').selectOption('move');await sourceCard.getByRole('button',{name:'保存方式',exact:true}).click();await idle();
 assert.equal(await sourceCard.locator('[data-source-mode]').inputValue(),'move');
 await nav('inbox');await page.locator('tbody tr').filter({hasText:'download.txt'}).waitFor();await idle();
 await page.getByRole('checkbox',{name:'选择 download.txt',exact:true}).check();
 await page.getByRole('button',{name:'预览已选计划',exact:false}).click();
 const moveButton=page.getByRole('button',{name:'确认归档并删除转移项原件（1 项）',exact:true});await moveButton.waitFor();
 assert(await page.locator('dialog').getByText('转移归档：复用已校验副本，本次仅清理原件。',{exact:true}).isVisible());
 const held=fs.openSync(path.join(fixture.downloads,'download.txt'),'r');
 try{await moveButton.click();await idle();}finally{fs.closeSync(held);}
 assert(fs.existsSync(path.join(fixture.downloads,'download.txt')));
 await nav('history');const retained=await page.evaluate(()=>S.operations.find(r=>r.status==='source_retained').id_handle);
 await page.locator(`tr[data-row="${retained}"]`).click();
 await page.getByRole('button',{name:'重新检查并清理原件…',exact:true}).click();
 await page.getByRole('button',{name:'确认清理原件',exact:true}).click();await idle();
 assert(!fs.existsSync(path.join(fixture.downloads,'download.txt')));
 assert(fs.existsSync(path.join(fixture.root,'01_课程学习/测试课程/download.txt')));
 await nav('review');await page.locator('tbody tr').filter({hasText:'unknown.txt'}).click();
 await page.locator('#file-purpose').fill('这是测试课程的专用学习笔记');
 await page.getByRole('button',{name:'补充说明并让AI分类',exact:true}).click();await page.getByRole('button',{name:'发送这 1 项至 DeepSeek',exact:true}).waitFor();
 assert(await page.locator('dialog').getByText('你补充的性质与用途：这是测试课程的专用学习笔记',{exact:true}).isVisible());
 await page.getByRole('button',{name:'发送这 1 项至 DeepSeek',exact:true}).click();await page.getByRole('button',{name:'执行这 1 项',exact:true}).waitFor();
 assert(await page.getByRole('heading',{name:'AI已结合补充信息确定归属 · 归档预览',exact:true}).isVisible());
 await page.getByRole('button',{name:'执行这 1 项',exact:true}).click();await idle();
 assert(fs.existsSync(path.join(fixture.root,'01_课程学习/测试课程/unknown.txt')));
 await nav('ai');assert.equal(await page.evaluate(()=>S.boot.library_audit),null);
 async function audit(){await page.getByRole('button',{name:'全库位置复核…',exact:true}).click();await page.locator('#audit-batches').fill('2');await page.getByRole('button',{name:'确认开始全库复核',exact:true}).click();await idle();}
 await audit();const first=await page.evaluate(()=>S.boot.library_audit);assert(first.approved>=2);assert.equal(first.files_moved,false);
 await audit();const second=await page.evaluate(()=>S.boot.library_audit);assert(second.cache_hits>=2);assert(second.ai_items<first.ai_items);
 assert(fs.existsSync(path.join(fixture.root,'00_收件箱/note.txt')));assert(fs.existsSync(path.join(fixture.root,'00_收件箱/other.txt')));
 assert.deepEqual(errors,[]);
 console.log('PASS: selectable copy/move per inbox, verified copy, existing archive reuse, explicit deletion preview, occupied source retained, history cleanup retry, AI classification and cached whole audit.');
}
main().catch(async e=>{console.error(e);if(page){console.error(await page.locator('#notice').textContent());console.error(await page.locator('dialog').textContent());}process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(fixture)fs.writeFileSync(fixture.stop,'stop');else child.kill();});
