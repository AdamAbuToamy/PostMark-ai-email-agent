import { approveMockConfirmations, openAdvancedSettings } from './ui_smoke_helpers.mjs'
import { spawn } from 'node:child_process'
import { setTimeout as pause } from 'node:timers/promises'
import { pathToFileURL } from 'node:url'
import assert from 'node:assert/strict'
const {chromium}=await import(process.argv[2]?pathToFileURL(process.argv[2]).href:'playwright')
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',BATCH3_1_FIXTURE:'1',BATCH3_2_FIXTURE:'1',BATCH3_3_FIXTURE:'1',BATCH3_4_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser,page
try {
  const base='http://127.0.0.1:8012'
  for(let i=0;i<60;i++){if(server.exitCode!==null)throw Error('Fixture exited');try{if((await fetch(base+'/api/health')).ok)break}catch{}if(i===59)throw Error('Fixture unavailable');await pause(200)}
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),args:['--no-sandbox']})
  page=await browser.newPage({viewport:{width:1360,height:940},reducedMotion:'reduce'});page.setDefaultTimeout(10000)
 await approveMockConfirmations(page)
  const errors=[];page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=500)errors.push('HTTP '+r.status()+' '+r.url())});page.on('pageerror',error=>errors.push(error.message));await page.route('https://fonts.googleapis.com/**',route=>route.abort())
  const goto=async path=>{await page.goto(base+path);if(path.startsWith('/settings'))await openAdvancedSettings(page)}
  const get=path=>page.request.get(base+path).then(r=>r.json())
  const state=()=>get('/fixture/state')
  const headers={'X-Email-Agent-Action':'message-management','Origin':'http://127.0.0.1:8000'}
  async function send(text){await page.getByLabel('Message',{exact:true}).fill(text);const response=page.waitForResponse(r=>r.url()===base+'/api/chat/personal'&&r.request().method()==='POST');await page.getByRole('button',{name:'Send',exact:true}).click();const value=await response;assert.equal(value.status(),200);await page.waitForFunction(()=>document.querySelector('#chat-message')?.value==='');return {data:await value.json(),headers:await value.request().allHeaders()}}
  await goto('/chat?account=personal')
  const step=await send('ممكن تراجع مراسلات المتجر وتقترح ترتيبها؟')
  assert.equal(step.data.agent_requests,2);assert.equal(step.data.results[0].matched_count,3)
  const chosen=step.data.results[1].preview.payload.items[0].message_id
  assert.equal((await get('/api/local-messages/personal/'+chosen)).source,'automatic')
  const preview=page.getByRole('region',{name:'Action preview'}).last()
  await preview.getByRole('checkbox',{name:'I reviewed this exact action and account.',exact:true}).check();await preview.getByRole('button',{name:'Confirm local change',exact:true}).click();await preview.getByText('The confirmed action succeeded.',{exact:true}).waitFor()
  assert.equal((await get('/api/local-messages/personal/'+chosen)).source,'manual');assert.equal((await get('/api/local-messages/university/'+chosen)).source,'automatic')
  const searched=await send('اعطني ميلات الي اكسبرس');const second=searched.data.results[0].messages[1].message_id
  const ordinal=await send('انقل الثانية إلى Orders');assert.equal(ordinal.data.results[0].preview.payload.items[0].message_id,second);assert.equal((await state()).model_calls,2)
  await page.getByLabel('More chat actions').click();await page.getByRole('button',{name:'Clear all history',exact:true}).click();await page.getByText('What would you like to organize?',{exact:true}).waitFor()
  assert.equal((await page.request.get(base+'/api/actions/personal/'+ordinal.data.results[0].preview.id)).status(),404)
  const provider=await send('Could you explain the rate limit issue?');assert.ok(provider.data.text.includes('provider rate limit'));assert.equal((await state()).model_calls,3)
  assert.equal((await page.request.post(base+'/api/chat/personal/safety-ceiling',{headers,data:{limit:3,approved:true}})).status(),200)
  const exhausted=await send('Could you offer a different plan?');assert.ok(/safety|wait briefly/i.test(exhausted.data.text))
  const local=await send('اعطني ميلات الي اكسبرس');assert.equal(local.data.results[0].matched_count,3);assert.equal(await page.locator('.v5-chat-usage').count(),0);assert.equal((await state()).model_calls,3)
  await page.screenshot({path:'/tmp/postmark-batch3_4-agent.png',fullPage:true})
  await goto('/settings?account=personal');await page.getByLabel('Active model',{exact:true}).waitFor();await page.getByRole('checkbox',{name:'Save chat history on this device',exact:true}).click();await page.getByText('Privacy preference saved.',{exact:true}).waitFor();await page.getByRole('button',{name:'Save AI preferences',exact:true}).click();await page.getByText('AI preferences saved. No model request was sent.',{exact:true}).waitFor()
  await goto('/chat?account=personal');await page.getByText(/Temporary chat ·/).waitFor()
  const temporary=await send('اعطني ميلات الي اكسبرس');assert.equal(temporary.data.results[0].matched_count,3)
  const pending=await send('انقل الثانية إلى Orders');await page.reload();await page.getByText('What would you like to organize?',{exact:true}).waitFor()
  const oldToken=pending.headers['x-postmark-chat-session'];assert.ok(oldToken)
  // Explicit end is idempotent even if pagehide already delivered it.
  await page.request.post(base+'/api/chat/personal/session/end',{headers:{...headers,'X-Postmark-Chat-Session':oldToken}})
  assert.equal((await page.request.post(base+'/api/chat/personal/actions/'+pending.data.results[0].preview.id+'/confirm',{headers:{...headers,'X-Postmark-Chat-Session':oldToken},data:{approved:true}})).status(),404)
  await goto('/subscriptions?account=personal')
  const reolink=page.locator('.v5-subscription-row').filter({hasText:'offers.reolink.example'})
  await reolink.getByText('Technical Details',{exact:true}).click();await reolink.getByText('Advanced verification',{exact:true}).click();await reolink.getByRole('button',{name:'Check unsubscribe eligibility…',exact:true}).click()
  const approval=page.getByRole('region',{name:'Action preview'});await approval.getByText('Verified signed destination: vendor.invalid',{exact:true}).waitFor();assert.equal((await state()).unsubscribe_events.length,0)
  await approval.getByRole('checkbox',{name:'Approve unsubscribe from offers.reolink.example',exact:true}).check();await approval.getByRole('checkbox',{name:'I reviewed this exact action and account.',exact:true}).check();await approval.getByRole('button',{name:'Confirm selected unsubscribe requests',exact:true}).click();await approval.getByText('Approved requests were accepted; future delivery is not confirmed stopped.',{exact:true}).waitFor()
  assert.equal((await state()).unsubscribe_events.length,1);await reolink.getByText('Request Submitted',{exact:true}).waitFor()
  const manual=page.locator('.v5-subscription-row').filter({hasText:'manual.news.example'})
  await manual.getByText('Technical Details',{exact:true}).click();await manual.getByText('Manual Action',{exact:true}).waitFor();await manual.getByText('Advanced list information',{exact:true}).click();await manual.getByText('Manual actions and next steps',{exact:true}).click()
  await manual.getByRole('button',{name:'Review manual method 1',exact:true}).click();await manual.getByText('Recipient: leave@manual.example',{exact:true}).waitFor();assert.equal(await page.locator('a[href^="mailto:"]').count(),0)
  await manual.getByRole('button',{name:'Review manual method 2',exact:true}).click();const link=manual.getByRole('link',{name:'Open reviewed manual unsubscribe page',exact:true});await link.waitFor();assert.equal(await link.getAttribute('rel'),'noopener noreferrer');assert.equal((await state()).unsubscribe_events.length,1)
  await manual.getByRole('button',{name:'Mark handled locally',exact:true}).click();await manual.getByText(/Handled locally · delivery not verified/).waitFor();assert.equal((await state()).unsubscribe_events.length,1)
  await manual.getByRole('button',{name:'Show manual action history',exact:true}).click();await manual.getByText(/handled_locally/).waitFor();assert.equal(await manual.getByRole('button',{name:'Block sender unavailable',exact:true}).isDisabled(),true)
  await page.screenshot({path:'/tmp/postmark-batch3_4-manual.png',fullPage:true})
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:'/tmp/postmark-batch3_4-mobile.png',fullPage:true})
  assert.deepEqual(errors,[]);assert.equal((await state()).events.length,0);assert.equal((await state()).paid_calls,0)
  console.log('PASS Batch 3.4 Chromium: mocked arbitrary Arabic two-step agent, exact ordinal follow-up/approval/account isolation, cap vs provider failures, local quota bypass, optional temporary history/reload/clear, cryptographically verified Reolink fixture with unknown labels and explicit mocked POST, manual email/HTTPS review/handled audit, unavailable blocking, mobile. No live Gemini, Gmail writes or unsubscribe requests.')
} catch(error){if(page){console.error(await page.locator('main').innerText().catch(()=>''));await page.screenshot({path:'/tmp/postmark-batch3_4-failure.png',fullPage:true}).catch(()=>{})}throw error}finally{await browser?.close();server.kill('SIGTERM')}
