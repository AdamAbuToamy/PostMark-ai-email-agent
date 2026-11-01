// One GET-only live layout/progress scenario; the active/idle lifecycle is a UI fixture.
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[];let override=null
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',r=>r.request().method()==='GET'&&r.request().url().startsWith(base+'/')&&!r.request().url().includes('/body')?r.continue():r.abort())
 await page.route(base+'/api/control/personal',r=>override?r.fulfill({status:200,contentType:'application/json',body:JSON.stringify(override)}):r.continue())
 for(const path of ['settings','categories'])for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000});await page.goto(base+'/'+path+'?account=personal')
  if(path==='settings'){
   const panel=page.getByRole('region',{name:'Connected AI qualification',exact:true});await panel.waitFor()
   const diagnostics=panel.locator('details').filter({has:page.getByText('Advanced Diagnostics',{exact:true})}).first()
   assert.equal(await diagnostics.getAttribute('open'),null)
   await panel.getByText('Advanced Diagnostics',{exact:true}).click()
  }else{
   const bar=page.getByRole('progressbar',{name:'Organization of known messages',exact:true});await bar.waitFor()
   const p=await page.request.get(base+'/api/control/personal').then(r=>r.json())
   assert.equal(p.progress.total,p.progress.organized+p.progress.waiting_gmail+p.progress.waiting_ai+p.progress.failed_blocked)
   assert.ok(Number(await bar.getAttribute('aria-valuenow'))<=Number(await bar.getAttribute('aria-valuemax')))
   assert.equal(await page.locator('.pm-activity-history').getAttribute('open'),null)
  }
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${path}/${width} horizontal overflow`)
  await page.screenshot({path:`/tmp/quick-progress-${path}-${width}.png`,fullPage:true})
 }
 const real=await page.request.get(base+'/api/control/personal').then(r=>r.json())
 override=structuredClone(real);override.activity.active=[{id:999999,provider:'UI fixture',model:'fixture-only-no-inference',task:'Classifying',started_at:Date.now()/1000-12,result:'processing'}]
 await page.reload();const live=page.locator('.pm-live-model');await live.waitFor();const before=await live.innerText();await page.waitForTimeout(1200);assert.notEqual(await live.innerText(),before)
 override.activity.active=[];override.activity.state='Waiting';await page.reload();await page.getByText('AI idle — next work queued',{exact:true}).waitFor();assert.equal(await page.locator('.pm-live-model').count(),0)
 override=null;await page.goto(base+'/categories?account=university');await page.getByRole('progressbar',{name:'Organization of known messages',exact:true}).waitFor()
 assert.deepEqual(errors,[])
 console.log('PASS live progress arithmetic, collapsed diagnostics/history, desktop/mobile layout, account switching. Active elapsed/idle transition verified with a labelled UI-only fixture. GET-only; no external model/Gmail requests.')
}finally{await browser.close()}
