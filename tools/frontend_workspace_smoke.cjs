// Synthetic delayed tasks and 180 persisted rows; never sends real data to AI.
const {chromium}=require('playwright');
const {spawn}=require('child_process');
const readline=require('readline');
const fs=require('fs');const path=require('path');const assert=require('assert/strict');
const root=path.resolve(__dirname,'..');
const child=spawn(process.env.PYTHON||'python',['-X','utf8',path.join(__dirname,'web_fixture.py'),'--workspace-fixture'],{cwd:root,windowsHide:true});
let fixture,browser,page;
async function main(){
 fixture=await new Promise((resolve,reject)=>{readline.createInterface({input:child.stdout}).once('line',s=>{try{resolve(JSON.parse(s));}catch(e){reject(e);}});child.stderr.on('data',d=>process.stderr.write(d));child.once('error',reject);});
 browser=await chromium.launch({headless:true,channel:'msedge'});page=await browser.newPage({viewport:{width:1440,height:980}});
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(fixture.url);await page.waitForFunction(()=>!!S.views.inbox?.job);
 await page.locator('nav [data-page=settings]').click();await page.getByRole('heading',{name:'规则管理',exact:true}).waitFor();
 await page.waitForFunction(()=>S.views.inbox.unread>0&&!S.views.inbox.job);
 assert.equal(await page.evaluate(()=>S.page),'settings');
 assert(await page.locator('nav [data-page=inbox] .nav-task').isVisible());
 await page.locator('nav [data-page=inbox]').click();await page.locator('tbody tr').filter({hasText:'note.txt'}).waitFor();
 assert.equal(await page.evaluate(()=>S.views.inbox.unread),0);
 // A pending AI connection test must not navigate back or overwrite history.
 await page.locator('nav [data-page=ai]').click();await page.locator('#ai-key').waitFor();
 await page.getByRole('button',{name:'测试连接',exact:true}).click();await page.waitForFunction(()=>!!S.views.ai.job);
 await page.locator('nav [data-page=history]').click();await page.locator('tbody tr').first().waitFor();
 await page.waitForFunction(()=>S.views.ai.unread>0&&!S.views.ai.job);
 assert.equal(await page.evaluate(()=>S.page),'history');
 assert(await page.locator('nav [data-page=ai] .nav-task').isVisible());
 // Scroll the list, select a low row, and keep details visible at the right.
 const box=page.locator('#rows');await box.hover();await page.mouse.wheel(0,10000);
 await page.locator('tbody tr').last().click();
 const layout=await page.evaluate(()=>({list:document.querySelector('#rows').scrollTop,detail:document.querySelector('#detail').getBoundingClientRect().top,heading:document.querySelector('#detail h3').getBoundingClientRect().top,global:window.scrollY,height:innerHeight}));
 assert(layout.list>100);assert(layout.heading>=layout.detail&&layout.heading<layout.height);assert.equal(layout.global,0);
 assert.equal(await page.locator('tbody tr').count(),100);
 await page.getByRole('button',{name:'下一页',exact:true}).click();await page.waitForFunction(()=>S.views.history.historyOffset===100&&!S.acting);
 assert((await page.locator('tbody tr').count())>0);
 await page.reload();await page.locator('tbody tr').filter({hasText:'note.txt'}).waitFor();
 await page.locator('nav [data-page=history]').click();await page.getByRole('heading',{name:'操作记录',exact:true}).waitFor();
 await page.locator('tbody tr').first().waitFor();assert((await page.evaluate(()=>S.views.history.historyTotal))>=182);
 await page.setViewportSize({width:850,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);
 console.log('PASS: navigation during scan/AI, per-page unread badges, no stale render/navigation, persisted seven-day activity, pagination, independent list/detail scrolling and compact desktop layout.');
}
main().catch(async e=>{console.error(e);if(page)console.error(await page.locator('#notice').textContent());process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(fixture)fs.writeFileSync(fixture.stop,'stop');else child.kill();});
