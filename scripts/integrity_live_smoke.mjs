// GET-only production check. Observe already-authorized workload; never submit AI.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',r=>r.request().method()==='GET'&&r.request().url().startsWith(base+'/')&&!r.request().url().includes('/body')?r.continue():r.abort())
 await page.goto(base+'/categories?account=personal')
 const bar=page.getByRole('progressbar',{name:'Organization of known messages',exact:true});await bar.waitFor()
 assert.ok(Number(await bar.getAttribute('aria-valuenow'))>3000,'Cumulative progress was replaced by a recent-run count')
 let observed=null,finished=null
 for(let i=0;i<140;i++){
  const res=await page.request.get(base+'/api/control/personal?activity_only=true'),a=await res.json()
  const run=a.active.find(r=>r.worker_id&&r.provider==='gemini')
  if(run){
   await page.waitForFunction(model=>document.querySelector('.pm-live-model')?.textContent?.includes(model),run.model,{timeout:7000})
   const current=await page.request.get(base+'/api/control/personal?activity_only=true').then(r=>r.json())
   if(current.active.some(r=>r.id===run.id)){
    observed=run;await page.screenshot({path:'/tmp/integrity-working-desktop.png',fullPage:true});break
   }
  }
  await page.waitForTimeout(500)
 }
 assert.ok(observed,'No actual new Gemini execution observed; do not claim live cloud proof')
 for(let i=0;i<35;i++){
  const a=await page.request.get(base+'/api/control/personal?activity_only=true').then(r=>r.json())
  if(!a.active.some(r=>r.id===observed.id)){finished=true;break}
  await page.waitForTimeout(500)
 }
 assert.ok(finished,'Observed execution did not finalize within check window')
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000})
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow')
  assert.equal(await page.getByRole('group',{name:'Group status',exact:true}).getByRole('button').count(),6)
 }
 await page.screenshot({path:'/tmp/integrity-working-mobile.png',fullPage:true})
 const now=await page.request.get(base+'/api/control/personal').then(r=>r.json())
 assert.equal(now.progress.total,now.progress.organized+now.progress.waiting_gmail+now.progress.waiting_ai+now.progress.failed_blocked)
 assert.deepEqual(errors,[])
 fs.writeFileSync('/tmp/integrity-live-proof.json',JSON.stringify({observed,finished,progress:now.progress,active:now.activity.active,completed_1h:now.activity.completed_1h}))
 console.log('PASS actual Gemini running shown during inference, execution leaves active state, cumulative count >3000, disjoint denominator, six visible tabs, desktop/mobile layout. GET-only; zero extra AI requests. Attempt '+observed.id)
}finally{await browser.close()}
