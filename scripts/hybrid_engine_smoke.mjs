// Isolated fixture UI. No real provider requests, mail reads or Gmail writes.
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {setTimeout as pause} from 'node:timers/promises'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',UX_PRODUCT_FIXTURE:'1',ORGANIZATION_FIXTURE:'1',FOCUSED_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser
try{
 const base='http://127.0.0.1:8012'
 for(let i=0;i<80;i++){try{if((await fetch(base+'/api/health')).ok)break}catch{}await pause(200)}
 browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[]
 page.on('pageerror',e=>errors.push(e.message));await page.route('https://fonts.googleapis.com/**',r=>r.abort())
 await page.goto(base+'/settings?account=personal')
 const engine=page.getByRole('region',{name:'AI Engine',exact:true})
 await engine.getByRole('heading',{name:'AI Engine',exact:true}).waitFor()
 assert.equal(await engine.getByText('Private API key',{exact:true}).isVisible(),false)
 await engine.getByLabel('AI engine preference',{exact:true}).selectOption('local')
 await engine.getByRole('status').filter({hasText:'Saved.'}).waitFor()
 assert.equal((await page.request.get(base+'/api/hybrid/personal').then(r=>r.json())).preference,'local')
 assert.equal((await page.request.get(base+'/api/hybrid/university').then(r=>r.json())).preference,'auto')
 const groq=engine.getByRole('article',{name:'Groq provider',exact:true})
 await groq.getByRole('button',{name:'Add key',exact:true}).click()
 const setup=page.getByRole('dialog',{name:'Set up Groq',exact:true})
 await setup.getByLabel('New provider API key').fill('fixture-only-never-sent-externally')
 await setup.getByRole('button',{name:'Save key securely',exact:true}).click()
 await setup.getByRole('button',{name:'Test and discover models',exact:true}).waitFor()
 await setup.getByRole('button',{name:'Close',exact:true}).click()
 const state=await page.request.get(base+'/api/hybrid/personal').then(r=>r.json())
 assert.equal(state.providers.find(p=>p.provider==='groq').configured,true);assert.deepEqual(state.grants,[])
 assert.equal(JSON.stringify(state).includes('fixture-only-never-sent-externally'),false)
 assert.equal(await engine.getByLabel('New provider API key').count(),0)
 // Slow legacy grouping must not hide independent hybrid progress.
 let releaseGroups
 const groupsReady=new Promise(resolve=>{releaseGroups=resolve})
 await page.route('**/api/organization/personal?*',async route=>{await groupsReady;await route.continue()},{times:1})
 let releaseMessages
 const messagesReady=new Promise(resolve=>{releaseMessages=resolve})
 await page.route('**/api/category-mailbox/personal/other?*',async route=>{await messagesReady;await route.continue()},{times:1})
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000});await page.goto(base+'/categories?account=personal')
  await page.getByLabel('Mailbox progress',{exact:true}).waitFor({timeout:5000})
  releaseGroups()
  await page.getByRole('button',{name:'Open navigation',exact:true}).click()
  await page.getByRole('navigation',{name:'Primary navigation'}).getByRole('button',{name:'Other',exact:true}).click()
  const strip=page.getByLabel('Other AI organization',{exact:true});await strip.waitFor({timeout:5000});releaseMessages()
  await page.locator('.v5-message').first().waitFor()
  for(const label of ['Waiting for email content','Local AI queued / processing','Waiting for cloud analysis','Analyzed but still Other','Errors / blocked','Moved out · last 24h'])await strip.getByText(label,{exact:true}).waitFor()
  const progress=await page.request.get(base+'/api/hybrid/personal/other').then(r=>r.json())
  assert.equal(Object.values(progress.counts).reduce((a,b)=>a+b,0),progress.total)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:`/tmp/hybrid-fixture-${width}.png`,fullPage:true})
 }
 const final=await page.request.get(base+'/fixture/state').then(r=>r.json());assert.equal(final.model_calls,0);assert.equal(final.events.length,0);assert.deepEqual(errors,[])
 console.log('PASS simple hybrid settings, private credentials, account preference isolation, no inferred provider consent, all-Other disjoint counts, per-message states and desktop/mobile layout. Providers mocked/offline; no real AI or Gmail.')
}finally{if(browser)await browser.close();server.kill('SIGTERM')}
