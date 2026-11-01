import {spawn} from 'node:child_process'
import {setTimeout as pause} from 'node:timers/promises'
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',SEMANTIC_REVIEW_FIXTURE:'1',MAIL_MANAGEMENT_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser
try{
 const base='http://127.0.0.1:8012'
 for(let i=0;i<70;i++){try{if((await fetch(base+'/api/health')).ok)break}catch{}await pause(200)}
 browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[]
 page.on('pageerror',e=>errors.push(e.message));await page.route('**/*',r=>r.request().url().startsWith(base+'/')&&r.request().method()==='GET'?r.continue():r.abort())
 const get=async path=>{const r=await page.request.get(base+path);assert.equal(r.status(),200);return r.json()}
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000})
  for(const path of ['trash','spam']){
   await page.goto(base+'/'+path+'?account=personal');await page.locator('.v5-managed-row').first().waitFor()
   const layout=await page.evaluate(()=>{
    const section=document.querySelector('.v5-mail-management'),r=section.getBoundingClientRect(),pad=parseFloat(getComputedStyle(section).paddingLeft)
    const nodes=[...section.querySelectorAll('.v5-management-toolbar input,.v5-management-toolbar button,.v5-managed-row,.v5-actions-note,.v5-mailbox-tabs')]
    const header=section.querySelector('.v5-section-header h2').getBoundingClientRect();
    return {headerInset:header.left-r.left,headerPadding:getComputedStyle(section.querySelector('.v5-section-header')).paddingLeft,overflow:document.documentElement.scrollWidth>innerWidth,pad,inside:nodes.every(n=>{const b=n.getBoundingClientRect();return !b.width||(b.left>=r.left+pad-1&&b.right<=r.right-pad+1)}),clip:getComputedStyle(document.querySelector('.v5-floating-doodles')).clipPath,pointer:getComputedStyle(document.querySelector('.v5-floating-doodles')).pointerEvents}
   })
   assert.ok(layout.headerInset<layout.pad+4,JSON.stringify(layout));assert.equal(layout.overflow,false);assert.ok(layout.pad>=16);assert.equal(layout.inside,true,JSON.stringify({width,path,layout}));assert.equal(layout.clip,'none');assert.equal(layout.pointer,'none')
   await page.screenshot({path:`/tmp/semantic-review-${path}-${width}.png`,fullPage:true})
  }
  await page.goto(base+'/bills?account=personal');await page.getByRole('group',{name:'Smart sections'}).waitFor()
  const bills=await get('/api/category-mailbox/personal/bills?state=all'),receipt=bills.items.find(i=>i.message_id==='review-receipt-0')
  await page.getByText('Order receipt',{exact:true}).waitFor();assert.ok(receipt);assert.notEqual(receipt.section_id,'other');assert.equal(bills.sections.find(s=>s.id===receipt.section_id).name,'Receipt')
  const title=await page.locator('[title="AI interpretation; body input unavailable or subject only"]').count();assert.ok(title>0)
  await page.screenshot({path:`/tmp/semantic-review-bills-${width}.png`,fullPage:true})
  await page.goto(base+'/jobs?account=personal');await page.locator('.pm-job-status').first().waitFor();const labels=await page.locator('.pm-job-status').allTextContents();for(const value of ['Rejected','Offer','Interview'])assert.ok(labels.includes(value),value)
  await page.screenshot({path:`/tmp/semantic-review-jobs-${width}.png`,fullPage:true})
 }
 await page.emulateMedia({reducedMotion:'no-preference'})
 assert.equal(await page.locator('.v5-doodle').first().evaluate(n=>getComputedStyle(n).animationDuration),'24s')
 await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.v5-doodle').first().evaluate(n=>getComputedStyle(n).animationName),'none')
 const state=await get('/fixture/state');assert.equal(state.model_calls,0);assert.equal(state.events.length,0);assert.deepEqual(errors,[])
 console.log('PASS desktop/mobile Trash+Spam interior bounds, long text/toolbar, balanced noninteractive reduced-motion background, receipt semantic alias → visible facet, multilingual job outcome badges, API joins/provenance. Zero AI/Gmail calls.')
}finally{if(browser)await browser.close();server.kill('SIGTERM')}
