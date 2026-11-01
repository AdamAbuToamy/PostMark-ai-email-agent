// Production acceptance: GET-only, no body fetch, AI submission or Gmail action.
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',out=readFileSync('/tmp/full-mailbox-release.txt','utf8').trim()
const browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
const errors=[],writes=[]
async function protect(page){
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',r=>{const q=r.request();if(q.method()!=='GET'||q.url().includes('/body')){writes.push(q.method());return r.abort()}return q.url().startsWith(base+'/')?r.continue():r.abort()})
}
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'});await protect(page)
 page.setDefaultTimeout(20000)
 const catalog={}
 for(const account of ['personal','university']){
  console.log('Checking published account',account)
  const data=await page.request.get(`${base}/api/organizer/${account}`).then(r=>r.json())
  const hybrid=await page.request.get(`${base}/api/hybrid/${account}/other`).then(r=>r.json())
  assert.equal(Object.values(hybrid.counts).reduce((a,b)=>a+b,0),hybrid.total)
  assert.equal(data.local_policy.enabled,true);assert.ok(data.automation.coverage.discovered>data.automation.bodies_complete)
  assert.ok(data.automation.coverage.waiting_gmail>0);assert.equal(data.automation.ai.authorized,account==='personal')
  catalog[account]=await page.request.get(`${base}/api/categories?account=${account}`).then(r=>r.json())
  for(const width of [1360,390]){
   console.log('Checking Other viewport',width)
   await page.setViewportSize({width,height:1000});await page.goto(`${base}/categories?account=${account}`)
   await page.getByLabel('Mailbox progress',{exact:true}).waitFor()
   for(const label of ['Discovered','Bodies available','Organized','Waiting for AI','Waiting for Gmail','Failed'])await page.getByLabel('Mailbox progress',{exact:true}).getByText(label,{exact:true}).waitFor()
   await page.getByRole('button',{name:'Open navigation',exact:true}).click()
   const nav=page.getByRole('navigation',{name:'Primary navigation'})
   await nav.getByRole('button',{name:'Other',exact:true}).waitFor()
   for(const c of catalog[account].filter(c=>c.id.startsWith('sem_')))await nav.getByRole('button',{name:c.name,exact:true}).waitFor()
   await nav.getByRole('button',{name:'Other',exact:true}).click()
   await page.getByLabel('Other AI organization',{exact:true}).waitFor()
   await page.getByRole('group',{name:'Smart sections'}).waitFor()
   await page.getByText('Local topics · unresolved messages are still queued for understanding.',{exact:true}).waitFor()
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   await page.addStyleTag({content:'.v5-message,.v5-organization-group ul{filter:blur(7px)}'})
   await page.screenshot({path:`${out}/whole-other-${account}-${width}.png`,fullPage:true})
  }
  await page.goto(`${base}/settings?account=${account}`)
  await page.getByRole('region',{name:'AI Engine',exact:true}).getByText('Free only',{exact:true}).waitFor()
  assert.equal(await page.getByLabel('Private API key',{exact:true}).isVisible(),false)
 }
 for(const c of catalog.personal.filter(c=>c.id.startsWith('sem_'))){
  assert.ok(!catalog.university.some(other=>other.id===c.id))
  const data=await page.request.get(`${base}/api/category-mailbox/personal/${c.id}?state=all`).then(r=>r.json());assert.ok(data.total>0)
  await page.goto(`${base}/categories?account=personal`);await page.getByLabel('Mailbox progress',{exact:true}).waitFor()
  await page.getByRole('button',{name:'Open navigation',exact:true}).click()
  const link=page.getByRole('navigation',{name:'Primary navigation'}).getByRole('button',{name:c.name,exact:true});await link.waitFor();assert.ok(await link.locator('svg').count());await link.click()
  await page.locator('.v5-message').first().waitFor();await page.reload();await page.locator('.v5-message').first().waitFor()
 }
 for(const path of ['review','cleanup-review','trash','spam']){
  await page.setViewportSize({width:390,height:1000});await page.goto(`${base}/${path}?account=personal`)
  if(path==='review')await page.getByRole('group',{name:'Review views'}).waitFor()
  else await page.getByPlaceholder('Search sender or subject…').waitFor()
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }
 await page.goto(base+'/cleanup-review?account=personal&view=protected')
 // Use the router's canonical filter spelling, then verify concise reason groups.
 await page.getByRole('tab',{name:/Protected/}).click()
 await page.getByRole('group',{name:'Retention reasons on this page'}).getByRole('button',{name:'Your choices',exact:true}).waitFor()
 await page.addStyleTag({content:'.v5-managed-row{filter:blur(7px)}'});await page.screenshot({path:out+'/whole-retention-mobile.png',fullPage:true})
 const fresh=await browser.newContext({viewport:{width:1360,height:1000}}),next=await fresh.newPage();await protect(next)
 await next.goto(base+'/categories?account=personal');await next.getByLabel('Mailbox progress',{exact:true}).waitFor()
 await next.getByRole('button',{name:'Open navigation',exact:true}).click()
 for(const c of catalog.personal.filter(c=>c.id.startsWith('sem_')))await next.getByRole('navigation',{name:'Primary navigation'}).getByRole('button',{name:c.name,exact:true}).waitFor()
 await fresh.close();assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
 console.log('PASS production progress/backlogs, all dynamic categories/sidebar/API counts, account isolation, Other topic provenance, retention groups, mobile bounds and fresh browser context. GET-only; no AI, body reads or Gmail actions.')
}finally{await browser.close()}
