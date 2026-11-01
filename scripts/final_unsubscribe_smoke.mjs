import { approveMockConfirmations, openAdvancedSettings } from './ui_smoke_helpers.mjs'
import { spawn } from 'node:child_process'
import { setTimeout as pause } from 'node:timers/promises'
import { pathToFileURL } from 'node:url'
import { mkdir } from 'node:fs/promises'
import assert from 'node:assert/strict'
const {chromium}=await import(process.argv[2]?pathToFileURL(process.argv[2]).href:'../browser/node_modules/playwright/index.mjs')
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',BATCH3_1_FIXTURE:'1',BATCH3_2_FIXTURE:'1',BATCH3_3_FIXTURE:'1',BATCH3_4_FIXTURE:'1',BATCH3_5_FIXTURE:'1',CORE_FEATURE_FIXTURE:'1',COMPLETION_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser,page
try {
 const base='http://127.0.0.1:8012',shots='./docs/screenshots'
 await mkdir(shots,{recursive:true})
 for(let i=0;i<60;i++){if(server.exitCode!==null)throw Error('Fixture exited');try{if((await fetch(base+'/api/health')).ok)break}catch{}if(i===59)throw Error('Fixture unavailable');await pause(200)}
 browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})})
 page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(15000)
 await approveMockConfirmations(page)
 const errors=[];page.on('pageerror',error=>errors.push(error.message));page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=500)errors.push('HTTP '+r.status())})
 await page.route('https://fonts.googleapis.com/**',route=>route.abort())
 const headers={'X-Email-Agent-Action':'message-management','Origin':'http://127.0.0.1:8000'}
 const goto=async path=>{await page.goto(base+path);if(path.startsWith('/settings'))await openAdvancedSettings(page)}
 const get=path=>page.request.get(base+path).then(r=>r.json())
 const state=()=>get('/fixture/state')
 const mode=value=>page.request.post(base+'/fixture/ai',{data:{mode:value}})
 const ai=()=>page.getByRole('region',{name:'AI assistant settings',exact:true})
 async function saveFree(){await ai().getByRole('checkbox',{name:'AI on',exact:true}).check();await ai().getByRole('radio',{name:'Free only · recommended',exact:true}).check();await ai().getByRole('checkbox',{name:/I confirm this Google project is free/}).check();await ai().getByRole('checkbox',{name:/Allow Gemini to process prompts/}).check();await ai().getByRole('button',{name:'Save AI preferences',exact:true}).click();await page.getByText('AI preferences saved. No model request was sent.',{exact:true}).waitFor()}
 async function send(text){await page.getByLabel('Message',{exact:true}).fill(text);const response=page.waitForResponse(r=>r.url()===base+'/api/chat/personal'&&r.request().method()==='POST');await page.getByRole('button',{name:'Send',exact:true}).click();const value=await response;assert.equal(value.status(),200);await page.waitForFunction(()=>document.querySelector('#chat-message')?.value==='');return value.json()}


 await goto('/subscriptions?account=personal')
 const panel=()=>page.getByRole('region',{name:'Unsubscribe',exact:true})
 const row=name=>page.locator('.v5-subscription-row').filter({has:page.getByText(name+'.website.example',{exact:true})})
 const posts=async()=>((await state()).website_events??[]).filter(event=>event.method==='POST')
 async function begin(name){await row(name).getByRole('button',{name:'Unsubscribe',exact:true}).click();const result=page.waitForResponse(r=>r.url().includes('/confirm-flow')&&r.request().method()==='POST');await panel().getByRole('button',{name:'Unsubscribe',exact:true}).click();const value=await (await result).json();await page.waitForFunction(()=>!document.querySelector('section[aria-label="Unsubscribe"] button')?.disabled);return value}
 async function close(){await panel().getByRole('button',{name:'Dismiss unsubscribe',exact:true}).click();await panel().waitFor({state:'detached'})}
 function noInternals(text){for(const phrase of ['confirmation uncertain','Inspect current page','End isolated session','seconds','https://','snapshot_id'])assert.equal(text.includes(phrase),false,phrase)}
 await row('or-direct').getByRole('button',{name:'Unsubscribe',exact:true}).waitFor()
 assert.equal(await row('or-direct').locator('.v5-subscription-row-actions button:visible').count(),3)
 assert.equal(await row('or-direct').getByRole('button',{name:'Open original message',exact:true}).isVisible(),false)
 await row('or-direct').screenshot({path:shots+'/final-unsubscribe-list-row.png'})
 let before=(await posts()).length;const direct=await begin('or-direct');await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();assert.equal((await posts()).length,before+1);noInternals(await panel().innerText());await panel().screenshot({path:shots+'/final-unsubscribe-success.png'});await close()
 before=(await posts()).length;await begin('or-stage');await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();assert.equal((await posts()).length,before+2);await close()
 before=(await posts()).length;await begin('or-explainer');await panel().getByRole('heading',{name:'Unable to unsubscribe — Open page manually',exact:true}).waitFor();assert.equal((await posts()).length,before);await close()
 await begin('or-completed');await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();assert.equal((await posts()).length,before);await close()
 const uncertain=await begin('or-uncertain');await panel().getByRole('heading',{name:'Request sent — confirmation pending',exact:true}).waitFor();noInternals(await panel().innerText());const count=(await posts()).length;await panel().screenshot({path:shots+'/final-unsubscribe-pending.png'});await close();await row('or-uncertain').getByRole('button',{name:'View status',exact:true}).click();await panel().getByRole('heading',{name:'Request sent — confirmation pending',exact:true}).waitFor();assert.equal((await posts()).length,count);assert.equal(await panel().getByRole('button',{name:'Unsubscribe',exact:true}).count(),0);await close()
 // The fallback is explicitly a different browser. No claim of same-session resume.
 const manualRequests=[]
 await page.context().route('https://letters.example.test/**',async route=>{manualRequests.push({method:route.request().method(),cookie:route.request().headers().cookie});await route.fulfill({contentType:'text/html',body:'<h1>Manual newsletter page</h1><button onclick="document.querySelector(\'h1\').textContent=\'Human finished the challenge\'">Complete challenge manually</button>'})})
 for(const identity of ['captcha','login']){
  const paused=await begin(identity);await panel().getByRole('heading',{name:'Needs your help — Open browser',exact:true}).waitFor();noInternals(await panel().innerText());assert.equal(await panel().getByRole('button',{name:'Continue',exact:true}).count(),0);if(identity==='captcha')await panel().screenshot({path:shots+'/final-unsubscribe-browser-help.png'});
  const popupPromise=page.waitForEvent('popup');await panel().getByRole('button',{name:'Open manually',exact:true}).click();const popup=await popupPromise;await popup.getByRole('heading',{name:'Manual newsletter page',exact:true}).waitFor();await popup.getByRole('button',{name:'Complete challenge manually',exact:true}).click();await popup.getByRole('heading',{name:'Human finished the challenge',exact:true}).waitFor();await panel().getByText(/Postmark cannot resume that session/).waitFor();assert.equal(await panel().getByRole('button',{name:'Continue',exact:true}).count(),0);const rejected=await page.request.post(base+'/api/subscriptions/personal/browser/'+paused.id+'/resume-preview',{headers,data:{}});assert.equal(rejected.status(),400);await popup.close();await close()
 }
 assert.ok(manualRequests.every(r=>r.method==='GET'&&!r.cookie))
 // The address is neither retrieved nor submitted by generic authorization.
 await begin('constantcontact');await panel().getByRole('button',{name:'Review my email address',exact:true}).waitFor();before=(await posts()).length;await panel().getByRole('button',{name:'Review my email address',exact:true}).click();await panel().getByText(/Allow personal@example.test to be submitted/).waitFor();assert.equal((await posts()).length,before);await panel().getByRole('button',{name:'Allow email and continue',exact:true}).click();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();await close()
 await begin('cc-multi');await panel().getByRole('button',{name:'Review my email address',exact:true}).click();await panel().getByText(/Allow personal@example.test to be submitted/).waitFor();await panel().getByRole('button',{name:'Allow email and continue',exact:true}).click();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();await close()
 await begin('customerio-topic');await panel().getByRole('checkbox',{name:'Stop receiving: Newsletter emails',exact:true}).check();await panel().getByRole('button',{name:'Unsubscribe from selected topics',exact:true}).click();await panel().getByRole('heading',{name:'Request sent — confirmation pending',exact:true}).waitFor();const scoped=(await posts()).at(-1);assert.equal(scoped.form.product[0],'on');assert.equal(scoped.form.all,undefined);assert.equal(scoped.form.newsletter,undefined);await close()
 await begin('customerio');await panel().getByRole('checkbox',{name:'Unsubscribe from all this sender’s marketing topics instead',exact:true}).check();await panel().getByRole('button',{name:'Unsubscribe from all topics',exact:true}).click();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();await close()
 const unsafe=await begin('private-redirect');await panel().getByRole('heading',{name:'Unable to unsubscribe — Open page manually',exact:true}).waitFor();assert.equal((await state()).website_events.some(e=>e.host==='127.0.0.1'),false);const blocked=await page.request.post(base+'/api/subscriptions/personal/'+unsafe.payload.subscription_id+'/manual-page',{headers,data:{action_id:unsafe.id}});assert.equal(blocked.status(),400);await close()
 const changed=await begin('redirect');await panel().getByRole('button',{name:'Allow new destination',exact:true}).waitFor();assert.equal((await state()).website_events.some(e=>e.host==='other.example.test'),false);await panel().getByRole('button',{name:'Allow new destination',exact:true}).click();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();await close()
 const audit=await get('/api/actions/personal/'+direct.id+'/audit');assert.ok(audit.some(item=>item.event==='website_final_request_started'));assert.equal((await state()).model_calls,0);assert.equal((await state()).events.length,0);assert.equal((await state()).paid_calls,0);assert.deepEqual(errors,[])
 await page.setViewportSize({width:390,height:844});await page.getByLabel('Subscription status',{exact:true}).selectOption('unsubscribed');await row('or-direct').getByRole('button',{name:'View status',exact:true}).click();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await panel().screenshot({path:shots+'/final-unsubscribe-mobile.png'})
 console.log('PASS final unsubscribe UX Chromium: exact OpenRouter question, pending named final button, intermediate POST, explanatory-only vs confirmed, no uncertain duplicate; compact defaults and hidden diagnostics; explicit address consent/topic/all; CAPTCHA/login separate-browser fallback and no fake Continue/resume, no cookies transferred; unsafe redirect/manual link rejected; changed destination approval; multi-step and mobile. All sites/Gmail mocked; no Gemini or live cancellations.')
} catch(error){if(page){console.error(await page.locator('main').innerText().catch(()=>''));await page.screenshot({path:'/tmp/postmark-final-unsubscribe-failure.png',fullPage:true}).catch(()=>{})}throw error}finally{await browser?.close();server.kill('SIGTERM')}
