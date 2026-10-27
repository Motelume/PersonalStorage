// Real offline OCR on synthetic images/PDFs; mock DeepSeek must remain unused.
const {chromium}=require('playwright'),{spawn}=require('child_process'),readline=require('readline');
const fs=require('fs'),path=require('path'),assert=require('assert/strict'),crypto=require('crypto');
const root=path.resolve(__dirname,'..');
const child=spawn(process.env.PYTHON||'python',['-X','utf8',path.join(__dirname,'web_fixture.py'),'--ocr-fixture','--ai-fixture'],{cwd:root,windowsHide:true});
let fixture,browser,page;
async function main(){
 fixture=await new Promise((resolve,reject)=>{readline.createInterface({input:child.stdout}).once('line',s=>{try{resolve(JSON.parse(s));}catch(e){reject(e);}});child.stderr.on('data',d=>process.stderr.write(d));child.once('error',reject);});
 const originals=['screenshot.png','native.pdf','mixed.pdf'].map(name=>({name,sha:crypto.createHash('sha256').update(fs.readFileSync(path.join(fixture.root,'00_收件箱',name))).digest('hex')}));
 browser=await chromium.launch({headless:true,channel:'msedge'});page=await browser.newPage({viewport:{width:1500,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 const idle=()=>page.waitForFunction(()=>!S.busy&&!S.acting);
 const nav=async p=>{await page.locator('nav [data-page='+p+']').click();await idle();if(p==='settings')await page.locator('[data-tab=runtime]').click();};
 await page.goto(fixture.url);await page.locator('tbody tr').filter({hasText:'screenshot.png'}).waitFor();await idle();
 let rows=await page.evaluate(()=>S.rows);assert.equal(rows.length,3);assert(rows.every(r=>r.decision==='move'));
 assert.equal(rows.find(r=>r.source.endsWith('screenshot.png')).text_source,'local_ocr');assert.equal(rows.find(r=>r.source.endsWith('native.pdf')).text_source,'native');assert.equal(rows.find(r=>r.source.endsWith('mixed.pdf')).text_source,'mixed');
 assert.equal(await page.evaluate(()=>S.boot.ai.calls_today),0);
 await page.locator('tbody tr').filter({hasText:'screenshot.png'}).click();await page.getByRole('button',{name:'读取详情与预览',exact:true}).click();await idle();
 assert((await page.locator('#ocr-source').textContent()).includes('缓存复用'));assert((await page.locator('#detail pre').first().textContent()).includes('course'));
 await page.getByRole('button',{name:'重新读取（忽略缓存）',exact:true}).click();await idle();assert((await page.locator('#ocr-source').textContent()).includes('本地 OCR'));
 await nav('settings');assert(await page.locator('#ocr-enabled').isChecked());await page.getByRole('button',{name:'本机识别自检',exact:true}).click();await idle();assert((await page.locator('#notice').textContent()).includes('识别通过'));
 await page.locator('#ocr-enabled').uncheck();await page.getByRole('button',{name:'保存 OCR 设置',exact:true}).click();await idle();await nav('inbox');
 rows=await page.evaluate(()=>S.rows);assert.equal(rows.find(r=>r.source.endsWith('screenshot.png')).ocr.status,'disabled');assert.equal(rows.find(r=>r.source.endsWith('screenshot.png')).decision,'review');assert.equal(rows.find(r=>r.source.endsWith('native.pdf')).decision,'move');
 await nav('settings');await page.locator('#ocr-enabled').check();await page.locator('#ocr-pages').selectOption('3');await page.getByRole('button',{name:'保存 OCR 设置',exact:true}).click();await idle();
 await page.getByRole('button',{name:'清空识别缓存…',exact:true}).click();await page.getByRole('button',{name:'确认清空缓存',exact:true}).click();await idle();assert.equal(await page.evaluate(()=>S.boot.ocr.cached_items),0);
 await nav('inbox');await idle();assert((await page.evaluate(()=>S.rows)).every(r=>r.decision==='move'));await page.reload();await page.locator('tbody tr').first().waitFor();await idle();assert((await page.evaluate(()=>S.rows)).every(r=>r.ocr.cache_hit));
 assert.equal(await page.evaluate(()=>S.boot.ai.calls_today),0);assert.deepEqual(errors,[]);
 for(const original of originals){assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(fixture.root,'00_收件箱',original.name))).digest('hex'),original.sha);}
 console.log('PASS: real image/native/mixed PDF OCR, existing rule classification without AI calls, source/cache preview, forced re-read, offline self-test, settings, disabled fallback, cache clear/persistence and unchanged originals.');
}
main().catch(async e=>{console.error(e);if(page)console.error(await page.locator('#notice').textContent());process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(fixture)fs.writeFileSync(fixture.stop,'stop');else child.kill();});
