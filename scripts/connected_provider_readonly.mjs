// Production UI inspection only. No mutations, provider calls or body requests.
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',browser=await chromium.launch({headless:true,chromiumSandbox:true})
try {
 const page=await browser.newPage({viewport:{width:1360,height:1050},reducedMotion:'reduce'}),errors=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',r=>r.request().method()==='GET'&&r.request().url().startsWith(base+'/')&&!r.request().url().includes('/body')?r.continue():r.abort())
 for(const account of ['personal','university']) {
  await page.goto(base+'/settings?account='+account)
  const engine=page.getByRole('region',{name:'AI Engine',exact:true});await engine.getByRole('heading',{name:'AI Engine',exact:true}).waitFor()
  await engine.getByRole('heading',{name:'Qualify connected AI providers',exact:true}).waitFor()
  await engine.getByRole('switch',{name:'Autonomous organization',exact:true}).filter({hasText:'On'}).waitFor({timeout:30000})
  for(const width of [1360,390]) {
   await page.setViewportSize({width,height:1050});await engine.scrollIntoViewIfNeeded()
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${account}/${width} overflow`)
   await page.screenshot({path:`/tmp/connected-providers-${account}-${width}.png`,fullPage:true})
  }
  const state=await page.request.get(base+'/api/hybrid/'+account).then(r=>r.json())
  for(const provider of ['gemini','groq','openrouter','mistral'])assert.equal(state.providers.find(p=>p.provider===provider).standing_free_access,true)
  assert.equal(await page.locator('input[type=password]').count(),0)
  const categories=await page.request.get(base+'/api/category-mailbox/'+account+'/categories').then(r=>r.json())
  const expected=account==='personal'?{'sem_view_test_results_c0c9c553':'file-text','sem_account_verification_8b2a3dbf':'shield','sem_welcome_message_d125b780':'message-circle'}:{'sem_event_invitation_9a065720':'calendar-days'}
  await page.getByRole('button',{name:'Open navigation',exact:true}).click()
  for(const [id,icon] of Object.entries(expected)){
   const category=categories.find(c=>c.id===id);assert.ok(category);assert.equal(category.icon_key,icon)
   const entry=page.getByRole('navigation',{name:'Primary navigation'}).getByRole('button',{name:category.name,exact:true})
   await entry.waitFor();assert.equal(await entry.locator('svg.lucide-'+icon).count(),1)
  }
  await page.screenshot({path:'/tmp/blocker-sidebar-'+account+'.png',fullPage:true})
 }
 assert.deepEqual(errors,[])
 console.log('PASS live SQLite-backed provider setup, standing access for both accounts, no saved key fields, desktop/mobile overflow checks. GET-only; no new inference or Gmail action initiated by browser.')
} finally {await browser.close()}
