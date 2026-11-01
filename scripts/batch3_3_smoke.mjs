import { approveMockConfirmations, openAdvancedSettings } from './ui_smoke_helpers.mjs'
import { spawn } from 'node:child_process'
import { setTimeout as pause } from 'node:timers/promises'
import { pathToFileURL } from 'node:url'
import assert from 'node:assert/strict'
const {chromium}=await import(process.argv[2]?pathToFileURL(process.argv[2]).href:'playwright')
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',BATCH3_1_FIXTURE:'1',BATCH3_2_FIXTURE:'1',BATCH3_3_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser,page
try{
  for(let i=0;i<60;i++){if(server.exitCode!==null)throw Error('Fixture exited');try{if((await fetch('http://127.0.0.1:8012/api/health')).ok)break}catch{}if(i===59)throw Error('Fixture unavailable');await pause(200)}
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),args:['--no-sandbox']})
  page=await browser.newPage({viewport:{width:1360,height:940},reducedMotion:'reduce'});page.setDefaultTimeout(10000)
 await approveMockConfirmations(page)
  const errors=[];page.on('pageerror',error=>errors.push(error.message));await page.route('https://fonts.googleapis.com/**',route=>route.abort())
  const base='http://127.0.0.1:8012'
  const goto=async path=>{await page.goto(base+path);if(path.startsWith('/settings'))await openAdvancedSettings(page)}
  const get=path=>page.request.get(base+path).then(r=>r.json())
  const state=()=>get('/fixture/state')
  async function send(text){await page.getByLabel('Message',{exact:true}).fill(text);const account=new URL(page.url()).searchParams.get('account');const response=page.waitForResponse(r=>r.url()===base+'/api/chat/'+account&&r.request().method()==='POST');await page.getByRole('button',{name:'Send',exact:true}).click();const value=await response;assert.equal(value.status(),200);await page.waitForFunction(()=>document.querySelector('#chat-message')?.value==='');return value.json()}
  await goto('/chat?account=personal')
  assert.equal((await get('/api/chat/personal/settings')).usage.remaining,0)
  for(const sentence of ['اعطني ميلات الي اكسبرس','اعرض رسائل علي اكسبرس','ابحث عن AliExpress']){
    const result=await send(sentence);assert.equal(result.processor,'local');assert.equal(result.ai_needed,false);assert.equal(result.results[0].matched_count,3);assert.equal((await state()).model_calls,0)
    assert.equal(await page.locator('.v5-chat-usage').count(),0)
  }
  await page.getByRole('button',{name:'Open Content-only fixture from Another Sender',exact:true}).first().click();await page.getByText('Indexed text from علي اكسبرس for personal',{exact:true}).waitFor();assert.equal(new URL(page.url()).searchParams.get('message'),'index-only');await page.getByRole('button',{name:'Close details',exact:true}).click()
  await page.getByRole('button',{name:'New chat',exact:true}).click();await page.getByText('What would you like to organize?',{exact:true}).waitFor()
  const paged=await send('ابحث عن Pagination marker');assert.equal(paged.results[0].matched_count,57)
  await page.getByText('57 matching messages · showing 50',{exact:true}).waitFor();await page.getByRole('button',{name:'Load more matching messages',exact:true}).click();await page.getByText('57 matching messages · showing 57',{exact:true}).waitFor();assert.equal(await page.locator('.v5-chat-search-results .v5-mail-row').count(),57)
  await page.getByRole('button',{name:'Open Pagination marker 056 from Pagination sender',exact:true}).click();await page.getByText('Full message fixture for personal.',{exact:true}).waitFor();assert.equal(new URL(page.url()).searchParams.get('message'),'pager-056');await page.getByRole('button',{name:'Close details',exact:true}).click()
  const blocked=await send('Could you help me make a plan?');assert.equal(blocked.processor,'not_processed');assert.ok(/safety|wait briefly/i.test(blocked.text));assert.equal(await page.locator('.v5-chat-usage').count(),0)
  const local=await send('اعطني ميلات الي اكسبرس');assert.equal(local.results[0].matched_count,3);assert.equal(await page.locator('.v5-chat-usage').count(),0);assert.equal((await state()).model_calls,0)
  const expected=await get('/api/chat/personal/messages?category=orders')
  const source=await send('حط رسائل الطلبات في Orders');assert.deepEqual(source.results[0].preview.payload.items.map(item=>item.message_id).sort(),expected.messages.map(item=>item.message_id).sort())
  const categoryPreview=page.getByRole('region',{name:'Action preview'}).last();await categoryPreview.getByRole('checkbox',{name:'I reviewed this exact action and account.',exact:true}).check();await categoryPreview.getByRole('button',{name:'Confirm local change',exact:true}).click();await categoryPreview.getByText('The confirmed action succeeded.',{exact:true}).waitFor()
  const moved=await send('انقل رسائل AliExpress إلى Orders');assert.equal(moved.results[0].preview.payload.items.length,3);const movePreview=page.getByRole('region',{name:'Action preview'}).last();await movePreview.getByRole('checkbox',{name:'I reviewed this exact action and account.',exact:true}).check();await movePreview.getByRole('button',{name:'Confirm local change',exact:true}).click();await movePreview.getByText('The confirmed action succeeded.',{exact:true}).waitFor()
  assert.equal((await get('/api/local-messages/personal/ali-promo')).category_id,'orders');assert.equal((await get('/api/local-messages/university/ali-promo')).category_id,'promotions')
  await page.screenshot({path:'/tmp/postmark-batch3_3-arabic.png',fullPage:true})
  await goto('/chat?account=university');const academic=await send('ورجيني ايميلات الجامعة');assert.equal(academic.results[0].matched_count,1);assert.equal(academic.results[0].messages[0].account_id,'university')
  await goto('/subscriptions?account=personal')
  const esp=page.locator('.v5-subscription-row').filter({hasText:'updates.openrouter.example'})
  await esp.getByText('Technical Details',{exact:true}).click();await esp.getByText('Advanced verification',{exact:true}).click();await esp.getByRole('button',{name:'Check unsubscribe eligibility…',exact:true}).waitFor();
  const checkedResponse=page.waitForResponse(r=>r.url()===base+'/api/subscriptions/personal/preview'&&r.request().method()==='POST')
  await esp.getByRole('button',{name:'Check unsubscribe eligibility…',exact:true}).click();const checked=await (await checkedResponse).json();const item=checked.payload.items[0]
  assert.equal(item.eligible,true);assert.equal(item.proof.alignment,'not_exact');assert.equal(item.proof.author_domain,'openrouter.ai')
  const review=page.getByRole('region',{name:'Action preview'})
  await review.getByText('External signing domain · additional review required',{exact:true}).waitFor();assert.equal((await state()).unsubscribe_events.length,0)
  await review.getByRole('button',{name:'Open original for manual review',exact:true}).click();await page.getByText('Full message fixture for personal.',{exact:true}).waitFor();await page.getByRole('button',{name:'Close details',exact:true}).click()
  const approve=review.getByRole('button',{name:'Confirm selected unsubscribe requests',exact:true})
  await review.getByRole('checkbox',{name:'Approve unsubscribe from updates.openrouter.example',exact:true}).check();await review.getByRole('checkbox',{name:'I reviewed this exact action and account.',exact:true}).check();assert.equal(await approve.isDisabled(),true)
  const headers={'X-Email-Agent-Action':'message-management','Origin':'http://127.0.0.1:8000'}
  const confirmPath='/api/subscriptions/personal/actions/'+checked.id+'/confirm'
  assert.equal((await page.request.post(base+confirmPath,{headers,data:{approved:true,approved_targets:[item.subscription_id]}})).status(),400)
  assert.equal((await state()).unsubscribe_events.length,0)
  await review.getByRole('checkbox',{name:'Acknowledge external signer for updates.openrouter.example',exact:true}).check()
  await page.screenshot({path:'/tmp/postmark-batch3_3-external-signer.png',fullPage:true})
  await approve.click();await review.getByText('Approved requests were accepted; future delivery is not confirmed stopped.',{exact:true}).waitFor();assert.equal((await state()).unsubscribe_events.length,1)
  const audit=await get('/api/actions/personal/'+checked.id+'/audit');assert.ok(JSON.stringify(audit).includes('not_established'))
  assert.equal((await page.request.post(base+confirmPath,{headers,data:{approved:true,approved_targets:[item.subscription_id],approved_third_party_targets:[item.subscription_id]}})).status(),200)
  assert.equal((await state()).unsubscribe_events.length,1)
  const unsafe=page.locator('.v5-subscription-row').filter({hasText:'unsafe.list.example'});await unsafe.getByText('Technical Details',{exact:true}).click();await unsafe.getByText('Advanced verification',{exact:true}).click();await unsafe.getByRole('button',{name:'Check unsubscribe eligibility…',exact:true}).click()
  await page.getByRole('region',{name:'Action preview'}).getByText('Unsubscribe destination is not a public address',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Confirm selected unsubscribe requests',exact:true}).count(),0)
  assert.equal((await state()).events.length,0);assert.equal((await state()).model_calls,0);assert.equal((await state()).paid_calls,0)
  await page.setViewportSize({width:390,height:844});await goto('/chat?account=personal');await page.getByRole('button',{name:'New chat',exact:true}).waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:'/tmp/postmark-batch3_3-mobile.png',fullPage:true})
  assert.deepEqual(errors,[])
  console.log('PASS Batch 3.3 Chromium: exact failing Arabic sentence and all variants with zero quota; indexed-body search and real message navigation; 57-result local pagination; quota warning limited to AI-needed turns; exact confirmed category previews/account isolation; independently signed third-party evidence, manual original, extra acknowledgment enforced in UI/API, mocked POST audit/replay; private URL rejected; mobile. No live Gmail modifications, Gemini calls or unsubscribe requests.')
}catch(error){if(page){console.error(await page.locator('main').innerText().catch(()=>''));await page.screenshot({path:'/tmp/postmark-batch3_3-failure.png',fullPage:true}).catch(()=>{})}throw error}finally{await browser?.close();server.kill('SIGTERM')}
