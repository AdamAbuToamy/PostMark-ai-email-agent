import {spawn} from 'node:child_process'
import {setTimeout as pause} from 'node:timers/promises'
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',FOCUSED_FIXTURE:'1',UX_PRODUCT_FIXTURE:'1',MAIL_MANAGEMENT_FIXTURE:'1',ORGANIZATION_FIXTURE:'1',BATCH3_2_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser
try{
 const base='http://127.0.0.1:8012'
 for(let i=0;i<70;i++){try{if((await fetch(base+'/api/health')).ok)break}catch{}if(i===69)throw Error('Fixture unavailable');await pause(200)}
 browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[]
 page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)errors.push('API '+r.status())});await page.route('https://fonts.googleapis.com/**',r=>r.abort())
 const paths=['overview','categories','review','orders','jobs','settings','spam','trash','subscriptions','cleanup-review']
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000})
  for(const path of paths){
   await page.goto(base+'/'+path+'?account=personal')
   if(path==='settings')await page.getByRole('heading',{name:'Privacy',exact:true}).waitFor()
   else if(['overview','categories','review'].includes(path))await page.locator('.v5-organization .pm-overview-numbers').waitFor()
   else if(['orders','jobs'].includes(path))await page.getByRole('group',{name:'Smart sections'}).waitFor()
   else if(path==='subscriptions')await page.getByLabel('Subscription status',{exact:true}).waitFor()
   else await page.locator('.v5-mailbox-pagination').waitFor()
   const layout=await page.evaluate(()=>{
    const stack=document.querySelector('.pm-page-stack'),children=[...stack.children].filter(n=>n.getBoundingClientRect().height>0),gaps=children.slice(1).map((n,i)=>n.getBoundingClientRect().top-children[i].getBoundingClientRect().bottom)
    const headers=[...document.querySelectorAll('h1,h2,h3')].filter(n=>n.getBoundingClientRect().height>0)
    return {overflow:document.documentElement.scrollWidth>innerWidth,gaps,headingsContained:headers.every(n=>{let box=n.getBoundingClientRect();for(let p=n.parentElement;p;p=p.parentElement){if(getComputedStyle(p).overflow==='hidden'){let r=p.getBoundingClientRect();if(box.top<r.top-1||box.left<r.left-1)return false}}return true})}
   })
   assert.equal(layout.overflow,false,path+' '+width);assert.ok(layout.gaps.every(g=>g>=23),path+' sibling gaps');assert.equal(layout.headingsContained,true,path+' heading clipping')
   for(const selected of await page.locator('.v5-mailbox-tabs button[aria-pressed=true],.pm-section-chips button[aria-pressed=true],button[role=tab][aria-selected=true]').all())assert.notEqual(await selected.evaluate(n=>getComputedStyle(n).backgroundColor),'rgba(0, 0, 0, 0)')
   await page.screenshot({path:`/tmp/focused-${path}-${width}.png`,fullPage:true})
  }
 }
 await page.goto(base+'/jobs?account=personal');await page.locator('.pm-job-status').first().waitFor()
 const labels=await page.locator('.pm-job-status').allTextContents();for(const value of ['Applied / Received','Interview','Rejected','Offer','Unknown'])assert.ok(labels.includes(value),value)
 await page.goto(base+'/categories/research?account=personal');await page.getByRole('group',{name:'Smart sections'}).getByRole('button',{name:/River ecology/}).waitFor()
 await page.getByRole('group',{name:'Smart sections'}).getByRole('button',{name:/River ecology/}).click();await page.locator('.v5-selectable-row').nth(1).waitFor();assert.equal(await page.locator('.v5-selectable-row').count(),2)
 const get=path=>page.request.get(base+path).then(r=>r.json())
 const state=await get('/fixture/state');assert.equal(state.model_calls,0);assert.equal(state.events.length,0)
 await page.getByRole('button',{name:/All types/}).click();await page.locator('.v5-selectable-row').nth(3).waitFor();await page.getByText('Optional AI section suggestions',{exact:true}).click()
 const ai=page.locator('.v5-semantic-advice');await ai.getByRole('checkbox').nth(0).check();await ai.getByRole('checkbox').nth(1).check()
 await ai.getByRole('button',{name:'Preview AI analysis budget'}).click();await ai.getByRole('region',{name:'AI analysis budget preview'}).waitFor();assert.equal((await get('/fixture/state')).model_calls,0);assert.equal(await ai.getByRole('region',{name:'AI analysis budget preview'}).locator('li').count(),2)
 const headers={'X-Email-Agent-Action':'message-management'}
 const setup=await page.request.post(base+'/api/chat/personal/simple-settings',{headers,data:{enabled:true,plan:'free',free_project_confirmed:true,paid_approved:false,processing_consent:true,save_history:true}});assert.equal(setup.status(),200)
 await ai.getByRole('checkbox',{name:'I approve sending these summaries within this request cap.'}).check();await ai.getByRole('button',{name:'Run bounded AI review'}).click();await ai.getByText('Research follow-ups',{exact:true}).waitFor();assert.equal((await get('/fixture/state')).model_calls,1)
 await page.emulateMedia({reducedMotion:'no-preference'});assert.equal(await page.locator('.v5-doodle').first().evaluate(n=>getComputedStyle(n).animationDuration),'24s')
 assert.ok(await page.locator('.v5-doodle').first().evaluate(n=>+getComputedStyle(n).opacity<=.2))
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))});await page.waitForFunction(()=>document.querySelector('.v5-floating-doodles').dataset.paused==='true');assert.equal(await page.locator('.v5-doodle').first().evaluate(n=>getComputedStyle(n).animationPlayState),'paused')
 await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.v5-doodle').first().evaluate(n=>getComputedStyle(n).animationName),'none')
 assert.deepEqual(errors,[]);console.log('PASS 10 pages desktop/mobile screenshots, shared spacing, selected controls, employment states, generic custom category discovery, bounded approved mock AI section advice, quiet/hidden/reduced-motion background. No live providers.')
}finally{if(browser)await browser.close();server.kill('SIGTERM')}
