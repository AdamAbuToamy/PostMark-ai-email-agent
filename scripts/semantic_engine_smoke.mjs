import {spawn} from 'node:child_process'
import {setTimeout as pause} from 'node:timers/promises'
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',FOCUSED_FIXTURE:'1',UX_PRODUCT_FIXTURE:'1',ORGANIZATION_FIXTURE:'1',BATCH3_2_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser
try {
 const base='http://127.0.0.1:8012'
 for(let i=0;i<70;i++){try{if((await fetch(base+'/api/health')).ok)break}catch{}await pause(200)}
 browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[]
 page.on('pageerror',e=>errors.push(e.message));await page.route('https://fonts.googleapis.com/**',r=>r.abort())
 await page.goto(base+'/categories?account=personal')
 const panel=page.getByLabel('Semantic organizer',{exact:true});await panel.getByRole('button',{name:'Organize my mail',exact:true}).click()
 await panel.getByRole('status').filter({hasText:'analyzed locally'}).waitFor()
 let state=await page.request.get(base+'/fixture/state').then(r=>r.json());assert.equal(state.model_calls,0)
 const headers={'X-Email-Agent-Action':'message-management'}
 assert.equal((await page.request.post(base+'/api/chat/personal/simple-settings',{headers,data:{enabled:true,plan:'free',free_project_confirmed:true,paid_approved:false,processing_consent:true,save_history:true}})).ok(),true)
 await panel.getByText('Advanced organization',{exact:true}).click();await panel.getByRole('button',{name:'Preview optional AI analysis'}).click()
 await panel.getByRole('status').filter({hasText:'exact redacted'}).waitFor()
 assert.equal(await panel.getByRole('button',{name:'Run approved scope'}).isDisabled(),true)
 state=await page.request.get(base+'/fixture/state').then(r=>r.json());assert.equal(state.model_calls,0)
 await panel.getByRole('checkbox',{name:'I approve this exact AI scope.'}).check();await panel.getByRole('button',{name:'Run approved scope'}).click()
 await panel.getByRole('status').filter({hasText:'completed'}).waitFor()
 const proposal=panel.locator('article').filter({has:page.getByRole('heading',{name:'Field research',exact:true})}).first()
 await proposal.locator('summary').click();await proposal.getByRole('button',{name:'Approve and apply',exact:true}).click()
 await panel.getByRole('status').filter({hasText:'applied:'}).waitFor()
 await page.getByRole('button',{name:'Open navigation',exact:true}).click()
 await page.getByRole('navigation',{name:'Primary navigation'}).getByRole('button',{name:'Field research',exact:true}).click()
 await page.getByRole('group',{name:'Smart sections'}).getByRole('button',{name:/Sampling/}).waitFor()
 await page.locator('.v5-message').first().click();await page.getByRole('dialog').getByText('Related message evidence',{exact:true}).click();await page.getByRole('dialog').getByText(/AI interpretation|Local evidence/).first().waitFor()
 await page.getByRole('button',{name:'Close details'}).click();await page.getByRole('dialog').waitFor({state:'hidden'})
 await page.screenshot({path:'/tmp/semantic-engine-desktop.png',fullPage:true})
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/semantic-engine-mobile.png',fullPage:true})
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 await page.reload();await page.getByRole('group',{name:'Smart sections'}).getByRole('button',{name:/Sampling/}).waitFor()
 state=await page.request.get(base+'/fixture/state').then(r=>r.json());assert.equal(state.model_calls,2);assert.equal(state.events.length,0)
 assert.deepEqual(errors,[]);console.log('PASS local organization, exact optional AI consent, typed mock semantic frames, representative approval/application, sidebar update without reload, new-category coherent facets, evidence timeline, persisted reload, mobile overflow. No live model or Gmail.')
}finally{if(browser)await browser.close();server.kill('SIGTERM')}
