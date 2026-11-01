import { approveMockConfirmations, openAdvancedSettings } from './ui_smoke_helpers.mjs'
import { spawn } from 'node:child_process'
import { setTimeout as pause } from 'node:timers/promises'
import { pathToFileURL } from 'node:url'
import assert from 'node:assert/strict'
const {chromium}=await import(process.argv[2]?pathToFileURL(process.argv[2]).href:'playwright')
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',BATCH3_1_FIXTURE:'1',BATCH3_2_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser,page
try{
  for(let i=0;i<60;i++){if(server.exitCode!==null)throw Error('Fixture exited');try{if((await fetch('http://127.0.0.1:8012/api/health')).ok)break}catch{}if(i===59)throw Error('Fixture unavailable');await pause(200)}
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),args:['--no-sandbox']})
  page=await browser.newPage({viewport:{width:1360,height:940},reducedMotion:'reduce'});page.setDefaultTimeout(10000)
 await approveMockConfirmations(page)
  const errors=[];page.on('pageerror',error=>errors.push(error.message));await page.route('https://fonts.googleapis.com/**',route=>route.abort())
  const goto=async path=>{await page.goto('http://127.0.0.1:8012'+path);if(path.startsWith('/settings'))await openAdvancedSettings(page)}
  const get=path=>page.request.get('http://127.0.0.1:8012'+path).then(r=>r.json())
  const state=()=>get('/fixture/state')
  async function send(text){await page.getByLabel('Message',{exact:true}).fill(text);const response=page.waitForResponse(r=>r.url()==='http://127.0.0.1:8012/api/chat/'+new URL(page.url()).searchParams.get('account')&&r.request().method()==='POST');await page.getByRole('button',{name:'Send',exact:true}).click();const value=await response;assert.equal(value.status(),200);await page.waitForFunction(()=>document.querySelector('#chat-message')?.value==='');return value.json()}
  await goto('/chat?account=personal');await page.getByRole('heading',{name:'Enable your Gemini assistant',exact:true}).waitFor();assert.equal(await page.getByRole('combobox',{name:'Chat mode'}).count(),0)
  assert.equal(await page.getByRole('button',{name:'Enable Gemini assistant',exact:true}).isDisabled(),true)
  const search=await send('اعرض رسائل AliExpress');assert.equal(search.processor,'local');assert.equal(search.results[0].messages.length,2);assert.equal((await state()).model_calls,0)
  await page.getByRole('checkbox',{name:'I allow Gemini processing under my saved Google project policy.',exact:true}).check();await page.getByRole('button',{name:'Enable Gemini assistant',exact:true}).click();await page.getByText('Gemini assistant · simple commands handled locally',{exact:true}).waitFor();assert.equal((await state()).model_calls,0)
  assert.equal((await get('/api/chat/personal/settings')).default_mode,'gemini')
  const move=await send('انقل رسائل AliExpress إلى Orders');assert.equal(move.results[0].preview.payload.items.length,2)
  const preview=page.getByRole('region',{name:'Action preview'}).last();await preview.getByRole('checkbox',{name:'I reviewed this exact action and account.',exact:true}).check();await preview.getByRole('button',{name:'Confirm local change',exact:true}).click();await preview.getByText('The confirmed action succeeded.',{exact:true}).waitFor();assert.equal((await get('/api/local-messages/personal/ali-promo')).category_id,'orders')
  await page.getByRole('button',{name:'New chat',exact:true}).click();await page.getByText('What would you like to organize?',{exact:true}).waitFor();assert.ok((await get('/api/chat/personal/conversations')).length>1)
  const trash=await send('احذف إعلانات Reolink إلى المهملات');const actionId=trash.results[0].preview.id;assert.equal(trash.results[0].preview.payload.items.length,2);await page.getByText('Read-only account',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Confirm Gmail Trash',exact:true}).isDisabled(),true)
  await page.getByRole('button',{name:'Clear conversation',exact:true}).click();await page.getByText('What would you like to organize?',{exact:true}).waitFor();assert.equal((await page.request.get('http://127.0.0.1:8012/api/actions/personal/'+actionId)).status(),404);assert.equal((await state()).events.length,0)
  await goto('/chat?account=university');const courses=await send('اعرض رسائل الجامعة الجديدة');assert.equal(courses.results[0].messages.length,1);assert.equal(courses.results[0].messages[0].account_id,'university');assert.equal((await state()).model_calls,0)
  await page.request.post('http://127.0.0.1:8012/api/chat/personal/safety-ceiling',{headers:{'X-Email-Agent-Action':'message-management','Origin':'http://127.0.0.1:8000'},data:{limit:1,approved:true}})
  await goto('/chat?account=personal');const ai=await send('Could you help me organize my week?');assert.equal(ai.processor,'gemini');assert.equal((await state()).model_calls,1);await page.locator('.v5-chat-turn').filter({hasText:'Could you help me organize my week?'}).last().waitFor();assert.equal(await page.locator('.v5-chat-usage').count(),0)
  const cached=await send('Could you help me organize my week?');assert.equal(cached.processor,'cached_gemini');assert.equal((await state()).model_calls,1);assert.equal(cached.processor,'cached_gemini')
  const exhausted=await send('Could you suggest another approach?');assert.equal(exhausted.processor,'not_processed');assert.ok(/safety|wait briefly/i.test(exhausted.text));assert.equal((await state()).model_calls,1)
  const local=await send('اعرض رسائل AliExpress');assert.equal(local.processor,'local');assert.equal(local.results[0].messages.length,2)
  await page.reload();await page.locator('.v5-chat-turn').filter({hasText:'Could you help me organize my week?'}).last().waitFor();assert.equal((await state()).model_calls,1)
  await goto('/promotions?account=personal');await page.getByRole('complementary',{name:'Subscription management'}).waitFor();await page.getByText('offers@reolink.example',{exact:true}).waitFor();assert.equal((await state()).header_reads.length,0)
  await page.getByRole('complementary',{name:'Subscription management'}).getByText('Details',{exact:true}).click();await page.getByRole('button',{name:'Read missing list headers',exact:true}).click();await page.getByRole('button',{name:'Read missing list headers',exact:true}).waitFor({state:'visible'});await page.waitForFunction(()=>!document.querySelector('aside[aria-label="Subscription management"] button')?.disabled)
  await page.locator('.v5-subscription-summary-row').filter({hasText:'offers@reolink.example'}).getByRole('button',{name:'Unsubscribe',exact:true}).waitFor();const reads=(await state()).header_reads.length;assert.ok(reads>0);assert.equal((await state()).header_reads.some(row=>row.message_id==='reolink-order'),false)
  await page.getByRole('button',{name:'View all subscriptions',exact:true}).click();await page.getByRole('heading',{name:'Mailing lists',exact:true}).waitFor();await page.getByRole('heading',{name:'Other senders',exact:true}).waitFor();await page.getByText('Details',{exact:true}).first().click();await page.getByRole('progressbar',{name:'Metadata discovery progress',exact:true}).waitFor();await page.getByText(/1 errors/).waitFor()
  assert.equal((await get('/api/subscriptions/personal')).items.filter(item=>item.kind==='header_offer').length,1)
  const grouped=(await get('/api/subscriptions/personal')).items.find(item=>item.kind==='header_offer');assert.equal(grouped.observed_count,2);assert.equal(grouped.automatic_available,false)
  await page.reload();assert.equal((await state()).header_reads.length,reads);assert.equal((await state()).events.length,0);assert.equal((await state()).unsubscribe_events.length,0)
  await page.screenshot({path:'/tmp/postmark-batch3_2-subscriptions.png',fullPage:true})
  await page.setViewportSize({width:390,height:844});await goto('/chat?account=personal');await page.getByRole('button',{name:'New chat',exact:true}).waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:'/tmp/postmark-batch3_2-chat-mobile.png',fullPage:true})
  assert.deepEqual(errors,[]);assert.equal((await state()).paid_calls,0)
  console.log('PASS Batch 3.2 Chromium: no mode selector; explicit onboarding and persisted Gemini default; zero AI for four Arabic commands; exact local and readonly Trash previews; New chat/archive and confirmed clear cancel approvals; account isolation; mocked AI response, exhausted quota, local alternatives and reload; sender/header-offer discovery, bounded scan/errors/cached reload, Promotions controls, mobile. No live Gemini/Gmail writes/unsubscribe POSTs.')
}catch(error){if(page){console.error(await page.locator('main').innerText().catch(()=>''));await page.screenshot({path:'/tmp/postmark-batch3_2-failure.png',fullPage:true}).catch(()=>{})}throw error}finally{await browser?.close();server.kill('SIGTERM')}
