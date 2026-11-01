import {openAIPreferences} from './ui_smoke_helpers.mjs'
import { spawn } from 'node:child_process'
import { setTimeout as pause } from 'node:timers/promises'
import { pathToFileURL } from 'node:url'
import { mkdir } from 'node:fs/promises'
import assert from 'node:assert/strict'
const {chromium}=await import(process.argv[2]?pathToFileURL(process.argv[2]).href:'../browser/node_modules/playwright/index.mjs')
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',BATCH3_1_FIXTURE:'1',BATCH3_2_FIXTURE:'1',BATCH3_3_FIXTURE:'1',BATCH3_4_FIXTURE:'1',BATCH3_5_FIXTURE:'1',CORE_FEATURE_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser,page
try {
 const base='http://127.0.0.1:8012',shots='./docs/screenshots'
 await mkdir(shots,{recursive:true})
 for(let i=0;i<60;i++){if(server.exitCode!==null)throw Error('Fixture exited');try{if((await fetch(base+'/api/health')).ok)break}catch{}if(i===59)throw Error('Fixture unavailable');await pause(200)}
 browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})})
 page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(15000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message));page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=500)errors.push('HTTP '+r.status())})
 await page.route('https://fonts.googleapis.com/**',route=>route.abort())
 const headers={'X-Email-Agent-Action':'message-management','Origin':'http://127.0.0.1:8000'}
 const goto=async path=>{await page.goto(base+path);if(path.startsWith('/settings'))await openAIPreferences(page)}
 const get=path=>page.request.get(base+path).then(r=>r.json())
 const state=()=>get('/fixture/state')
 const mode=value=>page.request.post(base+'/fixture/ai',{data:{mode:value}})
 const ai=()=>page.getByRole('region',{name:'AI assistant settings',exact:true})
 async function saveFree(){await ai().getByRole('checkbox',{name:'AI on',exact:true}).check();await ai().getByRole('radio',{name:'Free only · recommended',exact:true}).check();await ai().getByRole('checkbox',{name:/I confirm this Google project is free/}).check();await ai().getByRole('checkbox',{name:/Allow Gemini to process prompts/}).check();await ai().getByRole('button',{name:'Save AI preferences',exact:true}).click();await page.getByText('AI preferences saved. No model request was sent.',{exact:true}).waitFor()}
 async function send(text){await page.getByLabel('Message',{exact:true}).fill(text);const response=page.waitForResponse(r=>r.url()===base+'/api/chat/personal'&&r.request().method()==='POST');await page.getByRole('button',{name:'Send',exact:true}).click();const value=await response;assert.equal(value.status(),200);await page.waitForFunction(()=>document.querySelector('#chat-message')?.value==='');return value.json()}
 await mode('unverified');await goto('/settings?account=personal');await ai().getByRole('radio',{name:'Free only · recommended',exact:true}).waitFor();assert.equal(await ai().getByRole('radio',{name:'Free only · recommended',exact:true}).isChecked(),true)
 for(const label of ['Shared daily request limit','Verified free-tier RPD','Daily request allowance'])assert.equal(await page.getByLabel(label,{exact:true}).count(),0)
 await saveFree();await mode('legacy_zero');assert.equal((await get('/api/chat/personal/settings')).usage.chat_safety_ceiling,null);assert.equal((await state()).connection_calls,0)
 await page.screenshot({path:shots+'/core-ai-settings.png',fullPage:true})
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:shots+'/core-ai-mobile.png',fullPage:true});await page.setViewportSize({width:1360,height:1000})
 await goto('/chat?account=personal');for(let index=0;index<6;index++)assert.equal((await send('ساعدني في تنظيم مهامي لهذا الأسبوع '+index)).processor,'gemini')
 assert.equal((await state()).model_calls,6);assert.equal(await page.locator('.v5-chat-usage').count(),0)
 const limited=await send('Could you explain the rate limit issue?');assert.equal(limited.reason_code,'provider_rate_limited');assert.ok(/Google temporarily/i.test(limited.text));assert.equal((await state()).model_calls,7)
 const local=await send('اعطني ميلات الي اكسبرس');assert.equal(local.processor,'local');assert.equal(local.results[0].matched_count,15);assert.equal((await state()).model_calls,7)
 await page.screenshot({path:shots+'/core-chat.png',fullPage:true})
 await goto('/settings?account=personal');await ai().getByRole('radio',{name:'Paid',exact:true}).check();assert.equal(await ai().getByRole('button',{name:'Save AI preferences',exact:true}).isDisabled(),true);await ai().getByRole('checkbox',{name:/I separately authorize billed Gemini requests/}).check();await ai().getByRole('button',{name:'Save AI preferences',exact:true}).click();await page.getByText('AI preferences saved. No model request was sent.',{exact:true}).waitFor();assert.equal((await get('/api/chat/personal/settings')).usage.provider_policy.paid_approved,true);await saveFree();assert.equal((await get('/api/chat/personal/settings')).usage.provider_policy.paid_approved,false)
 await goto('/subscriptions?account=personal')
 const panel=()=>page.getByRole('region',{name:'Unsubscribe',exact:true})
 async function start(identity){const row=page.locator('.v5-subscription-row').filter({has:page.getByText(identity,{exact:true})});const before=(await state()).website_events.length;await row.getByRole('button',{name:'Unsubscribe',exact:true}).click();await panel().waitFor();assert.equal((await state()).website_events.length,before);return panel()}
 async function approve(){await panel().getByRole('button',{name:'Unsubscribe',exact:true}).click();await panel().getByRole('button',{name:'Dismiss unsubscribe',exact:true}).waitFor();await page.waitForFunction(()=>!document.querySelector('section[aria-label="Unsubscribe"] button')?.disabled);return panel()}
 async function close(){await panel().getByRole('button',{name:'Dismiss unsubscribe',exact:true}).click();await panel().waitFor({state:'detached'})}
 // Primary UI stays compact; independent cryptographic execution is still tested.
 await start('offers.vendor.invalid');assert.equal((await state()).unsubscribe_events.length,0);await panel().getByRole('button',{name:'Unsubscribe',exact:true}).click();await panel().getByRole('heading',{name:'Request sent — confirmation pending',exact:true}).waitFor();assert.equal((await state()).unsubscribe_events.length,1);await close()
 await start('simple.website.example');await page.screenshot({path:shots+'/core-unsubscribe-approval.png',fullPage:true});await approve();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();await panel().getByText('Technical Details',{exact:true}).click();await panel().getByText(/browser_scope_approved/).waitFor();await panel().screenshot({path:shots+'/core-unsubscribe-result.png'});await close()
 assert.equal((await state()).website_events.filter(e=>e.method==='POST').length,1)
 await start('multi.website.example');await approve();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();assert.ok((await state()).website_events.some(e=>e.path==='/confirm'));await close()
 for(const identity of ['login','captcha','information','scope','bad-action','open-only']){
  const before=(await state()).website_events.filter(e=>e.method==='POST').length;await start(identity+'.website.example');await approve();await panel().getByRole('heading',{name:/Needs your help|Unable to unsubscribe/}).waitFor();assert.equal((await state()).website_events.filter(e=>e.method==='POST').length,before);assert.equal(await panel().getByRole('button',{name:'Inspect current page without resubmitting',exact:true}).count(),0);if(identity==='captcha')await panel().screenshot({path:shots+'/core-unsubscribe-handoff.png'});await close()
 }
 for(const identity of ['unsafe','no-method']){await start(identity+'.website.example');await panel().getByRole('heading',{name:'Unable to unsubscribe — Open page manually',exact:true}).waitFor();await close()}
 await start('redirect.website.example');await approve();await panel().getByText(/moved to other.example.test/).waitFor();const beforeRedirect=(await state()).website_events.length;assert.equal((await state()).website_events.some(e=>e.host==='other.example.test'),false);await panel().getByRole('button',{name:'Allow new destination',exact:true}).click();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();assert.ok((await state()).website_events.length>beforeRedirect);await close()
 await start('submitted.website.example');await approve();await panel().getByRole('heading',{name:'Request sent — confirmation pending',exact:true}).waitFor();await close()
 const groups=(await get('/api/subscriptions/personal')).items;const simple=groups.find(i=>i.display_name==='simple.website.example');const action=await get('/api/actions/personal/'+simple.request_state.action_id);const beforeReplay=(await state()).website_events.length;assert.equal((await page.request.post(base+'/api/subscriptions/personal/browser/'+action.id+'/confirm',{headers,data:{approved:true}})).status(),200);assert.equal((await state()).website_events.length,beforeReplay);assert.equal((await page.request.post(base+'/api/subscriptions/university/browser/'+action.id+'/confirm',{headers,data:{approved:true}})).status(),404)
 const audit=await get('/api/actions/personal/'+action.id+'/audit');assert.ok(JSON.stringify(audit).includes('website_final_request_started'))
 assert.equal((await state()).website_events.every(e=>!e.authorization),true);assert.equal((await state()).events.length,0);assert.equal((await state()).paid_calls,0);assert.deepEqual(errors,[])
 console.log('PASS core Chromium production UI: simplified Free/consent/Paid revocation; six model turns with legacy zero cap; human Google429/local Arabic results; independently verified RFC path; actual isolated sandboxed Playwright single/multi-step mocked websites; observed Cancelled vs Request submitted; private URL, no-method, login, CAPTCHA, scope, data and prompt injection stops; changed-origin renewed approval; exact-list audit/replay/account isolation. No live provider or cancellation traffic.')
} catch(error){if(page){console.error(await page.locator('main').innerText().catch(()=>''));await page.screenshot({path:'/tmp/postmark-core-failure.png',fullPage:true}).catch(()=>{})}throw error}finally{await browser?.close();server.kill('SIGTERM')}
