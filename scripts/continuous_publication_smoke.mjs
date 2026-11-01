// Disposable SQLite + real API/React path. No production approval or external AI.
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {setTimeout as wait} from 'node:timers/promises'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',PUBLICATION_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser
try{
 const base='http://127.0.0.1:8012'
 for(let i=0;i<80;i++){try{if((await fetch(base+'/api/health')).ok)break}catch{}await wait(200)}
 browser=await chromium.launch({headless:true,chromiumSandbox:true})
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',r=>r.request().url().startsWith(base+'/')?r.continue():r.abort())
 await page.goto(base+'/categories?account=personal')
 const tabs=page.getByRole('group',{name:'Group status',exact:true});await tabs.waitFor()
 await tabs.getByRole('button',{name:'Suggestions',exact:true}).click()
 assert.equal(await tabs.getByRole('button').count(),6)
 await page.route('**/api/organization/personal?**',r=>r.fulfill({status:500,contentType:'application/json',body:'{"detail":"Fixture group loading failure"}'}),{times:1})
 await tabs.getByRole('button',{name:'Awaiting analysis',exact:true}).click();await page.getByRole('alert').filter({hasText:'Fixture group loading failure'}).waitFor()
 assert.equal(await tabs.getByRole('button').count(),6)
 await tabs.getByRole('button',{name:'Suggestions',exact:true}).click()
 const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Environmental observations',exact:true})});await card.waitFor()
 await card.getByText('Preview change · 3 messages',{exact:true}).click()
 await page.route('**/api/organizer/personal/proposals/*/decision',r=>r.fulfill({status:409,contentType:'application/json',body:'{"detail":"Fixture apply failure; retry remains available"}'}),{times:1})
 await card.getByRole('button',{name:'Approve and apply',exact:true}).click()
 await page.getByRole('alert').filter({hasText:'Fixture apply failure'}).waitFor()
 assert.equal((await page.request.get(base+'/fixture/publication').then(r=>r.json())).status,'suggested')
 await card.getByRole('button',{name:'Approve and apply',exact:true}).click()
 await tabs.getByRole('button',{name:'Applied',exact:true}).click()
 const open=page.getByRole('link',{name:'Open Environmental observations',exact:true});await open.waitFor()
 const proof=await page.request.get(base+'/fixture/publication').then(r=>r.json())
 assert.equal(proof.status,'applied');assert.equal(proof.members,3);assert.equal(proof.categories.length,1);assert.equal(proof.categories[0].icon_key,'file-text')
 const replay=await page.request.post(`${base}/api/organizer/personal/proposals/${proof.proposal_id}/decision`,{headers:{'X-Email-Agent-Action':'message-management'},data:{fingerprint:proof.fingerprint,approved:true}})
 assert.equal(replay.status(),200);assert.equal((await replay.json()).affected,0)
 await page.getByRole('button',{name:'Open navigation',exact:true}).click()
 const nav=page.getByRole('navigation',{name:'Primary navigation'}).getByRole('button',{name:'Environmental observations',exact:true});await nav.waitFor()
 assert.equal(await nav.locator('.pm-nav-count').innerText(),'3');assert.equal(await nav.locator('svg.lucide-file-text').count(),1)
 await nav.click();await page.locator('.v5-message').first().waitFor();assert.equal(await page.locator('.v5-message').count(),3)
 await page.reload();await page.locator('.v5-message').first().waitFor()
 await page.goto(base+'/categories?account=university');await tabs.waitFor()
 const other=await page.request.get(base+'/api/category-mailbox/university/categories').then(r=>r.json());assert.ok(!other.some(c=>c.id===proof.category_id))
 await page.goto(base+'/categories?account=personal');await tabs.waitFor()
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000})
  for(const title of ['Suggestions','Organized','Awaiting analysis','Protected','Applied','Dismissed']){
   await tabs.getByRole('button',{name:title,exact:true}).click();assert.equal(await tabs.getByRole('button').count(),6)
  }
  await tabs.getByRole('button',{name:'Applied',exact:true}).click();await open.waitFor()
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`/tmp/continuous-publication-${width}.png`,fullPage:true})
 }
 const final=await page.request.get(base+'/fixture/state').then(r=>r.json());assert.equal(final.model_calls,0);assert.deepEqual(errors,[])
 console.log('PASS real isolated SQLite approval+commit+3 memberships+icon/count sidebar+idempotent retry+refresh+account isolation+Applied link; same-tab/loading/error keep six tabs; desktop/mobile. Zero cloud calls; no production suggestion approved.')
}finally{if(browser)await browser.close();server.kill('SIGTERM')}
