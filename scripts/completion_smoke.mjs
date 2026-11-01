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

 await goto('/chat?account=personal');assert.equal((await send('Hello working model verification')).processor,'gemini')
 await goto('/settings?account=personal');assert.equal(await ai().locator('option[value="gemini-3.8-flash"]').count(),0);await page.request.post(base+'/api/chat/personal/model',{headers,data:{model:'gemini-3.8-flash',approved:true}});await goto('/settings?account=personal');await ai().getByRole('button',{name:'Switch to Gemini 3.5 Flash Lite',exact:true}).waitFor()
 const before=(await state()).model_calls
 await ai().getByRole('button',{name:'Validate selected model',exact:true}).click();await ai().getByRole('status').filter({hasText:'gemini-3.8-flash: model unavailable'}).waitFor();assert.equal((await state()).model_calls,before)
 await page.screenshot({path:shots+'/completion-model-validation.png',fullPage:true})
 await mode('model404');await goto('/chat?account=personal');const missing=await send('Explain this model failure');assert.equal(missing.reason_code,'model_unavailable');assert.equal(missing.diagnostic.http_status,404);assert.equal(missing.diagnostic.provider_status,'NOT_FOUND');await page.getByText(/This model was not found or is unavailable/).waitFor();assert.equal((await get('/api/chat/personal/settings')).model,'gemini-3.8-flash')
 await goto('/settings?account=personal');await ai().getByRole('button',{name:'Return to last verified model',exact:true}).click();await page.getByText('Returned to the last verified working model. No request was generated.',{exact:true}).waitFor();assert.equal((await get('/api/chat/personal/settings')).model,'gemini-3.5-flash-lite');assert.equal((await get('/api/chat/personal/settings')).usage.provider_policy.paid_approved,false)
 await mode('malformed');await goto('/chat?account=personal');assert.equal((await send('Explain another model issue')).reason_code,'malformed_output')
 await mode('success');await goto('/subscriptions?account=personal')
 const panel=()=>page.getByRole('region',{name:'Unsubscribe',exact:true})
 const posts=async()=>((await state()).website_events??[]).filter(e=>e.method==='POST')
 async function start(identity){const row=page.locator('.v5-subscription-row').filter({has:page.getByText(identity,{exact:true})});await row.getByRole('button',{name:'Unsubscribe',exact:true}).click();await panel().getByRole('button',{name:'Unsubscribe',exact:true}).click();await panel().getByRole('heading',{name:'Needs your help — Open browser',exact:true}).waitFor()}
 async function close(){await panel().getByRole('button',{name:'Dismiss unsubscribe',exact:true}).click();await panel().waitFor({state:'detached'})}
 await start('customerio.website.example');assert.equal((await posts()).length,0);await panel().getByRole('checkbox',{name:'Unsubscribe from all this sender’s marketing topics instead',exact:true}).check();await panel().getByRole('button',{name:'Unsubscribe from all topics',exact:true}).click();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();await close()
 await start('customerio-topic.website.example');await panel().getByRole('checkbox',{name:'Stop receiving: Newsletter emails',exact:true}).check();await panel().getByRole('button',{name:'Unsubscribe from selected topics',exact:true}).click();await panel().getByRole('heading',{name:'Request sent — confirmation pending',exact:true}).waitFor();const topicPost=(await posts()).at(-1);assert.equal(topicPost.form.product[0],'on');assert.equal(topicPost.form.newsletter,undefined);assert.equal(topicPost.form.all,undefined);await close()
 for(const item of ['constantcontact','cc-multi']){
  await start(item+'.website.example');const beforeAddress=(await posts()).length;await panel().getByRole('button',{name:'Review my email address',exact:true}).click();await panel().getByText(/Allow personal@example.test to be submitted/).waitFor();assert.equal((await posts()).length,beforeAddress);if(item==='constantcontact')await panel().screenshot({path:shots+'/completion-recipient-approval.png'});await panel().getByRole('button',{name:'Allow email and continue',exact:true}).click();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor();if(item==='cc-multi')await panel().screenshot({path:shots+'/completion-multistep-result.png'});await close()
 }
 const beforeAmbiguous=(await posts()).length
 const ambiguous=page.locator('.v5-subscription-row').filter({has:page.getByText('ambiguous.website.example',{exact:true})});await ambiguous.getByRole('button',{name:'Unsubscribe',exact:true}).click();await panel().getByRole('button',{name:'Unsubscribe',exact:true}).click();await panel().getByRole('heading',{name:'Request sent — confirmation pending',exact:true}).waitFor();assert.equal((await posts()).length,beforeAmbiguous+1);assert.equal(await panel().getByRole('button',{name:'Inspect current page without resubmitting',exact:true}).count(),0);await panel().screenshot({path:shots+'/completion-uncertain-handoff.png'});await close()
 const errored=page.locator('.v5-subscription-row').filter({has:page.getByText('visible-error.website.example',{exact:true})});await errored.getByRole('button',{name:'Unsubscribe',exact:true}).click();await panel().getByRole('button',{name:'Unsubscribe',exact:true}).click();await panel().getByRole('heading',{name:'Unable to unsubscribe — Open page manually',exact:true}).waitFor();await panel().getByText('Technical Details',{exact:true}).click();await panel().getByText(/This unsubscribe token expired/).waitFor();assert.equal((await posts()).length,beforeAmbiguous+1);await close()
 assert.equal((await state()).events.length,0);assert.equal((await state()).paid_calls,0);assert.deepEqual(errors,[])
 console.log('PASS completion Chromium actual UI: explicit metadata validation/model404/no fallback/confirmed last-working return; malformed output; Customer.io-style topic vs all separate approval; Constant Contact-style account email separate consent and multi-step confirmation; retained uncertain page/inspect only/no duplicate POST; visible error and local close. Mock websites/providers only.')
} catch(error){if(page){console.error(await page.locator('main').innerText().catch(()=>''));await page.screenshot({path:'/tmp/postmark-completion-failure.png',fullPage:true}).catch(()=>{})}throw error}finally{await browser?.close();server.kill('SIGTERM')}
