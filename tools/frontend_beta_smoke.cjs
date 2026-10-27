const {chromium}=require('playwright'),{spawn}=require('child_process'),readline=require('readline'),fs=require('fs'),path=require('path'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..'),child=spawn(process.env.PYTHON||'python',['-X','utf8',path.join(__dirname,'web_fixture.py'),'--beta-fixture'],{cwd:root,windowsHide:true});let fixture,browser,page;
async function main(){
 fixture=await new Promise((resolve,reject)=>{readline.createInterface({input:child.stdout}).once('line',s=>{try{resolve(JSON.parse(s));}catch(e){reject(e);}});child.stderr.on('data',d=>process.stderr.write(d));child.once('error',reject);});
 browser=await chromium.launch({headless:true,channel:'msedge'});page=await browser.newPage({viewport:{width:1550,height:1050}});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 const idle=()=>page.waitForFunction(()=>!S.busy&&!S.acting);const nav=async p=>{await page.locator(`nav [data-page=${p}]`).click();await idle();};const tab=async t=>{await page.locator(`[data-tab=${t}]`).click();};
 const wb=async a=>{await page.locator(`[data-wb=${a}]`).first().click();await idle();};const rm=async a=>{await page.locator(`[data-rm=${a}]`).first().click();await idle();};
 await page.goto(fixture.url);await page.locator('tbody tr').first().waitFor();await idle();
 await page.locator('#wb-task').selectOption('sample-task');await page.getByRole('button',{name:'扫描并智能分类',exact:false}).click();await idle();
 assert((await page.evaluate(()=>S.rows)).every(r=>r.decision==='move'||r.decision==='conflict'));
 await nav('settings');assert((await page.locator('.wb-typed').textContent()).includes('identity'));await page.locator('[data-wb-pref="0/reason"]').fill('edited existing field');await rm('save');
 await tab('rules');await wb('advanced');await page.locator('[data-wb-group=""]').selectOption('any');
 await page.locator('[data-wb=expr-add][data-kind=not]').first().click();assert(await page.locator('.wb-expression').count()>=2);
 await page.locator('[data-wb-expr="/any/1/not"][data-key=field]').selectOption('name');await page.locator('[data-wb-expr="/any/1/not"][data-key=value]').fill('draft');await rm('save');
 await rm('test-tab');await page.locator('[data-rm-synthetic=text]').fill('course');await rm('simulate');assert((await page.locator('.rm-result').textContent()).includes('命中'));
 await tab('rules');await wb('ai-rules');await page.locator('#wb-ai-goal').fill('Use a course keyword to suggest a rule');await page.locator('#wb-submit').click();await idle();
 const beforeCalls=await page.evaluate(()=>S.boot.ai.calls_today);assert(beforeCalls===0||beforeCalls===1);
 await wb('ai-run');await wb('ai-accept-rule');await page.locator('dialog [data-action=close-modal]').click();await nav('history');await nav('settings');await tab('rules');assert((await page.locator('#rm-rule-list').textContent()).includes('合成AI规则候选'));
 await nav('overview');await wb('index');await page.locator('#wb-budget').fill('100');await page.locator('#wb-submit').click();await idle();await page.locator('#wb-search-query').fill('course');await wb('search');assert(await page.locator('.wb-search-results article').count()>=1);
 await page.locator('#wb-search-mode').selectOption('semantic');await page.locator('#wb-search-query').fill('course material');await wb('search');assert((await page.locator('.wb-search-results').textContent()).includes('本地语义相似度'));
 await nav('settings');await tab('runtime');await page.locator('summary').filter({hasText:'备份与恢复向导'}).click();const download=page.waitForEvent('download');await wb('backup');await download;
 await page.locator('[data-wb=restore]').first().click();await idle();await page.getByRole('button',{name:'确认执行',exact:true}).waitFor();await page.getByRole('button',{name:'取消',exact:true}).click();
 await page.locator('summary').filter({hasText:'通用规则包升级'}).click();await wb('package');await page.getByRole('button',{name:'取消',exact:true}).click();
 await page.locator('summary').filter({hasText:'任务继续与运行诊断'}).click();
 // Lose an acknowledgement after the server accepted the job, then lose one poll.
 // Transport retry must reuse the request ID and create exactly one task.
 const countDiagnostics=()=>page.evaluate(async()=>(await api('activity')).rows.filter(r=>r.task?.action==='wb-diagnostics').length);
 const diagnosticsBefore=await countDiagnostics();let submissions=0,polls=0;
 await page.route('**/api/wb-diagnostics',async route=>{if(++submissions===1){await route.fetch();await route.abort('connectionreset');}else await route.continue();});
 await page.route('**/api/jobs/*',async route=>{if(++polls===1)await route.abort('connectionreset');else await route.continue();});
 await wb('diagnostics');assert((await page.locator('dialog pre').textContent()).includes('database_integrity'));
 assert.equal(submissions,2);assert(polls>=2);assert.equal(await countDiagnostics(),diagnosticsBefore+1);
 await page.unroute('**/api/wb-diagnostics');await page.unroute('**/api/jobs/*');await page.locator('dialog [data-action=close-modal]').click();
 // Discard the uncommitted rule draft before a real task lifecycle operation.
 await tab('test');await rm('reset');await rm('reset-confirm');await tab('tasks');await page.locator('#wb-lifecycle').selectOption('completed');await wb('lifecycle');await wb('confirm');
 assert(fs.existsSync(path.join(fixture.root,'01_课程学习/测试课程（已完成）')));assert(fs.existsSync(path.join(fixture.root,'00_收件箱/note.txt')));
 assert.deepEqual(errors,[]);console.log('PASS beta nested rules, legacy edits, task delivery, AI candidates, fulltext/semantic search, backup restore preview, package upgrade, diagnostics and confirmed lifecycle rename');
}
main().catch(async e=>{console.error(e);if(page)console.error(await page.locator('#notice').textContent());process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(fixture)fs.writeFileSync(fixture.stop,'stop');else child.kill();});
