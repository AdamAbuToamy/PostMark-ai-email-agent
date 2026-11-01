// One production GET-only scenario: error -> retry -> stale state -> accounts -> refresh.
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000}}),errors=[];let failures=1
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',r=>r.request().method()==='GET'&&r.request().url().startsWith(base+'/')&&!r.request().url().includes('/body')?r.continue():r.abort())
 await page.route(base+'/api/hybrid/personal',r=>failures-->0?r.fulfill({status:503,contentType:'application/json',body:'{"detail":"Synthetic local status failure"}'}):r.continue())
 await page.goto(base+'/settings?account=personal')
 let engine=page.getByRole('region',{name:'AI Engine',exact:true})
 await engine.getByText('Status unavailable',{exact:true}).waitFor()
 assert.equal(await engine.locator('.pm-provider-state').filter({hasText:'Inactive'}).count(),0)
 await engine.getByRole('button',{name:'Retry status',exact:true}).click()
 await engine.getByRole('switch',{name:'Autonomous organization',exact:true}).filter({hasText:'On'}).waitFor()
 const live=await page.request.get(base+'/api/hybrid/personal').then(r=>r.json())
 assert.equal(await engine.getByLabel('Enable Groq',{exact:true}).isChecked(),!!live.providers.find(p=>p.provider==='groq').enabled)
 await page.waitForTimeout(2100);failures=1
 await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')))
 await engine.getByText('Showing last verified status.',{exact:false}).waitFor()
 assert.equal(await engine.getByLabel('Enable Groq',{exact:true}).isEnabled(),true)
 await engine.getByRole('button',{name:'Retry status',exact:true}).click()
 for(const account of ['university','personal']){
  await page.getByLabel('Choose email account',{exact:true}).first().selectOption(account)
  engine=page.getByRole('region',{name:'AI Engine',exact:true})
  await engine.getByRole('switch',{name:'Autonomous organization',exact:true}).filter({hasText:'On'}).waitFor()
  assert.equal(await engine.getByText('Checking…',{exact:true}).count(),0)
 }
 await page.reload();await engine.getByRole('switch',{name:'Autonomous organization',exact:true}).filter({hasText:'On'}).waitFor()
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000})
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
  await page.screenshot({path:`/tmp/ui-state-scheduling-${width}.png`,fullPage:true})
 }
 await page.goto(base+'/categories?account=personal')
 const summary=page.getByLabel('Hybrid AI status',{exact:true});await summary.waitFor()
 const state=await page.request.get(base+'/api/hybrid/personal').then(r=>r.json())
 assert.ok((await summary.innerText()).includes(`Local: ${state.local} · Cloud: ${state.cloud}`))
 assert.deepEqual(errors,[])
 console.log('PASS persisted cards; bounded failure; retry; verified state retained; accounts; refresh; Settings/organization consistency; desktop/mobile. GET-only, no inference.')
}finally{await browser.close()}
