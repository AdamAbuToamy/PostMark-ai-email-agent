import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const browser=await chromium.launch({headless:true,chromiumSandbox:true})
try {
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[],writes=[];let failProgress=false
 page.on('pageerror',e=>errors.push(e.message));page.on('response',async r=>{const u=new URL(r.url());if(u.pathname==='/api/control/personal'&&!u.search)console.log('progress response',r.status(),Date.now())})
 await page.route('**/api/**',r=>{
  const request=r.request(),url=new URL(request.url())
  if(request.method()!=='GET'){writes.push(url.pathname);return r.abort()}
  if(failProgress&&url.pathname==='/api/control/personal'&&!url.search)return r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Simulated transient status read failure'})})
  return r.continue()
 })
 await page.goto('http://127.0.0.1:8000/categories?account=personal')
 let panel=page.getByRole('region',{name:'Hybrid AI status'});await panel.getByRole('progressbar').waitFor({timeout:60000})
 const before=Number(await panel.getByRole('progressbar').getAttribute('aria-valuenow'));assert.ok(before>=4033)
 assert.ok((await panel.innerText()).includes('Last verified:'))
 failProgress=true;await page.reload();panel=page.getByRole('region',{name:'Hybrid AI status'});await panel.getByRole('progressbar').waitFor({timeout:10000})
 await panel.getByText(/Refresh delayed; retrying automatically/).waitFor({timeout:10000})
 assert.ok(Number(await panel.getByRole('progressbar').getAttribute('aria-valuenow'))>=before)
 failProgress=false
 await page.waitForFunction(()=>{const p=document.querySelector('[aria-label="Hybrid AI status"]');return p&&!p.textContent.includes('Refresh delayed; retrying automatically')},{},{timeout:35000}).catch(async e=>{console.log('panel at failure',await panel.innerText(),'hidden',await page.evaluate(()=>document.hidden));await page.screenshot({path:'/tmp/progress-recovery-failure.png'});throw e})
 await page.goto('http://127.0.0.1:8000/settings?account=personal')
 const zai=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Z.AI',exact:true})})
 await zai.getByRole('button',{name:'Change key'}).waitFor({timeout:30000});await zai.getByRole('button',{name:'Change key'}).click()
 const dialog=page.getByRole('dialog',{name:'Set up Z.AI'});await dialog.getByRole('button',{name:'Use saved connection'}).click()
 const select=dialog.locator('select[name=quality_model]');await select.selectOption('glm-4.5-flash')
 const form=select.locator('xpath=ancestor::form');await form.getByRole('checkbox').check()
 const last=await page.request.get('http://127.0.0.1:8000/api/hybrid/personal').then(r=>r.json()),provider=last.providers.find(p=>p.provider==='zai')
 assert.ok((await select.locator('option').allTextContents()).includes('glm-4.5-flash'))
 assert.ok(!(await select.locator('option').allTextContents()).includes('glm-5.3-flash'))
 assert.equal(await form.getByRole('button',{name:'Qualify selected model'}).isDisabled(),provider.last_evaluation?.status==='running')
 assert.ok((await dialog.innerText()).includes('Qualification previews do not consume requests'))
 await dialog.getByRole('button',{name:'Close',exact:true}).click()
 for(const width of [1360,390]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`/tmp/live-progress-repair-${width}.png`,fullPage:true})}
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
 console.log('PASS real persisted progress, session snapshot retained across reload/503, automatic recovery without app restart, exact GLM4.5 selection and meaningful qualification gating, paid model hidden, desktop/mobile. GET-only; no AI calls.')
} finally {await browser.close()}
