import {spawn} from 'node:child_process'
import {setTimeout as pause} from 'node:timers/promises'
import assert from 'node:assert/strict'
import {pathToFileURL} from 'node:url'
const {chromium}=await import(pathToFileURL(process.argv[2]??'./browser/node_modules/playwright/index.mjs').href)
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',ORGANIZATION_FIXTURE:'1',MAIL_MANAGEMENT_FIXTURE:'1',BATCH3_2_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser,page
try {
 const base='http://127.0.0.1:8012'
 for(let i=0;i<60;i++){try{if((await fetch(base+'/api/health')).ok)break}catch{}if(i===59)throw Error('Fixture unavailable');await pause(200)}
 browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
 page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'})
 const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)errors.push('API HTTP '+r.status())});await page.route('https://fonts.googleapis.com/**',r=>r.abort())
 await page.goto(base+'/categories?account=personal')
 const panel=()=>page.getByRole('region',{name:'Mail organization',exact:true})
 await panel().getByRole('heading',{name:/GitHub workflow failures/}).waitFor()
 await panel().getByRole('button',{name:'Organized',exact:true}).click();await panel().getByRole('heading',{name:/GitHub security alerts/}).waitFor();await panel().getByRole('button',{name:'Suggestions',exact:true}).click();await panel().getByRole('heading',{name:/GitHub workflow failures/}).waitFor()
 const failed=()=>panel().locator('.v5-organization-group').filter({has:page.getByRole('heading',{name:/GitHub workflow failures/})})
 await failed().getByRole('button',{name:'Approve category',exact:true}).click()
 await page.getByRole('region',{name:'Confirm group decision'}).getByRole('button',{name:'Confirm local group decision',exact:true}).click()
 await panel().getByText('Applied to 3 messages.',{exact:true}).waitFor()
 const rows=await page.request.get(base+'/api/classifications/personal?category=github_ci').then(r=>r.json());assert.equal(rows.items.length,3)
 let state=await page.request.get(base+'/fixture/state').then(r=>r.json());assert.equal(state.events.length,0);assert.equal(state.model_calls,0)
 await panel().getByRole('button',{name:'Applied',exact:true}).click();await panel().getByRole('heading',{name:/GitHub workflow failures/}).waitFor();assert.equal(await panel().getByRole('button',{name:'Approve category',exact:true}).count(),0);await page.screenshot({path:'docs/screenshots/organization-desktop.png'})
 await page.getByRole('button',{name:'Open navigation',exact:true}).click()
 const nav=page.getByRole('navigation',{name:'Primary navigation'})
 assert.equal(await nav.getByRole('button',{name:'Automatic Cleanup',exact:true}).count(),0)
 assert.equal(await nav.getByRole('button',{name:'Settings',exact:true}).count(),1)
 await nav.getByRole('button',{name:'Settings',exact:true}).click()
 for(const name of ['AI Assistant','Automatic Cleanup','Accounts','Privacy'])await page.getByRole('heading',{name,exact:true}).waitFor()
 // The historical session is user initiated, bounded, and uses persisted pages.
 await page.goto(base+'/cleanup-review?account=personal')
 await page.getByText('Mailbox coverage',{exact:true}).click()
 await page.getByLabel('Historical session page limit',{exact:true}).fill('1')
 await page.getByRole('button',{name:'Complete historical sync',exact:true}).click()
 await page.getByText('Historical sync session ended. Check coverage for remaining work.',{exact:true}).waitFor()
 const progress=await page.request.get(base+'/api/mail-management/personal/progress').then(r=>r.json())
 assert.equal(progress.gmail_history.pages_completed,1);assert.equal(progress.gmail_history.discovered,20);assert.equal(progress.gmail_history.fetched,20);assert.equal(progress.gmail_history.remaining_pages,null)
 state=await page.request.get(base+'/fixture/state').then(r=>r.json());assert.equal(state.events.length,0);assert.equal(state.model_calls,0)
 await page.setViewportSize({width:390,height:844});await page.goto(base+'/review?account=personal')
 await panel().getByRole('button',{name:'Applied',exact:true}).click();await panel().getByRole('heading',{name:/GitHub workflow failures/}).waitFor()
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 await page.screenshot({path:'docs/screenshots/organization-mobile.png'})
 // Optional semantic review remains idle until exact budget approval.
 const headers={'X-Email-Agent-Action':'message-management'}
 const setup=await page.request.post(base+'/api/chat/personal/simple-settings',{headers,data:{enabled:true,plan:'free',free_project_confirmed:true,paid_approved:false,processing_consent:true,save_history:true}});assert.equal(setup.status(),200)
 await panel().getByText('Advanced review',{exact:true}).click()
 await panel().getByText('Optional AI review of uncertain messages',{exact:true}).click()
 await panel().getByRole('checkbox',{name:'Your application interview',exact:true}).check()
 await panel().getByRole('button',{name:'Preview AI analysis budget',exact:true}).click()
 const budget=page.getByRole('region',{name:'AI analysis budget preview',exact:true})
 await budget.waitFor();state=await page.request.get(base+'/fixture/state').then(r=>r.json());assert.equal(state.model_calls,0)
 assert.equal(await budget.getByRole('button',{name:'Run bounded AI review',exact:true}).isDisabled(),true)
 await budget.getByRole('checkbox',{name:'I approve sending these summaries within this request cap.',exact:true}).check()
 await budget.getByRole('button',{name:'Run bounded AI review',exact:true}).click()
 await page.getByRole('region',{name:'AI classification suggestions',exact:true}).getByText(/1 requests · 0 errors · Analysis completed/).waitFor()
 state=await page.request.get(base+'/fixture/state').then(r=>r.json());assert.equal(state.model_calls,1);assert.equal(state.events.length,0)
 assert.deepEqual(errors,[])
 console.log('PASS organization Chromium: semantic subtypes, exact group approval, persisted patterns, no Gmail/AI effects, unified Settings navigation, bounded historical continuation and counters, mobile.')
} finally {await browser?.close();server.kill('SIGTERM')}
