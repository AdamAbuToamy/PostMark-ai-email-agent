import { approveMockConfirmations, openAdvancedSettings } from './ui_smoke_helpers.mjs'
import { spawn } from 'node:child_process'
import { setTimeout as pause } from 'node:timers/promises'
import { pathToFileURL } from 'node:url'
import assert from 'node:assert/strict'
const { chromium } = await import(process.argv[2] ? pathToFileURL(process.argv[2]).href : 'playwright')
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',BATCH3_1_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser,page
try {
  for(let i=0;i<60;i++){ if(server.exitCode!==null) throw Error('Fixture failed'); try{if((await fetch('http://127.0.0.1:8012/api/health')).ok)break}catch{} if(i===59)throw Error('Fixture unavailable');await pause(200) }
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),args:['--no-sandbox']})
  page=await browser.newPage({viewport:{width:1360,height:940},reducedMotion:'reduce'})
  page.setDefaultTimeout(10000)
 await approveMockConfirmations(page)
  const errors=[], external=[]
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith('http://127.0.0.1:8012')&&!r.url().startsWith('https://fonts.'))external.push(r.url())})
  await page.route('https://fonts.googleapis.com/**',route=>route.abort())
  const goto=async path=>{await page.goto('http://127.0.0.1:8012'+path);if(path.startsWith('/settings'))await openAdvancedSettings(page)}
  const get=path=>page.request.get('http://127.0.0.1:8012'+path).then(r=>r.json())
  const state=()=>get('/fixture/state')
  const nav=()=>page.getByRole('navigation',{name:'Primary navigation'})
  await goto('/inbox?account=personal')
  await page.getByRole('button',{name:'Open navigation',exact:true}).click()
  await nav().getByText('Workspace',{exact:true}).waitFor();await nav().getByText('Categories',{exact:true}).waitFor();await nav().getByText('Assistant',{exact:true}).waitFor()
  const toggle=()=>nav().getByRole('button',{name:'Custom Categories',exact:true})
  await toggle().click();assert.equal(await toggle().getAttribute('aria-expanded'),'false')
  await page.reload();await page.getByRole('button',{name:'Open navigation',exact:true}).click();assert.equal(await toggle().getAttribute('aria-expanded'),'false')
  await goto('/orders?account=personal');await page.getByRole('button',{name:'Open navigation',exact:true}).click();assert.equal(await toggle().getAttribute('aria-expanded'),'true');await nav().getByRole('button',{name:'Security',exact:true}).waitFor();await nav().locator('[aria-current=page] .v5-nav-highlight').waitFor();assert.notEqual(await nav().locator('[aria-current=page] .v5-nav-highlight').evaluate(element=>getComputedStyle(element).backgroundColor),'rgba(0, 0, 0, 0)');await page.screenshot({path:'/tmp/postmark-batch3_1-sidebar.png',fullPage:true})
  await goto('/chat?account=personal')
  async function chat(text){await page.getByLabel('Message',{exact:true}).fill(text);await page.getByRole('button',{name:'Send',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#chat-message')?.value==='')}
  await chat('اعرض رسائل AliExpress');await page.locator('.v5-chat-message .v5-mail-row').first().waitFor();await page.getByRole('button',{name:'Use in next request',exact:true}).first().click()
  await chat('انقل هذه الرسالة إلى Security');const action=page.getByRole('region',{name:'Action preview'}).last();await action.getByRole('checkbox').check();await action.getByRole('button',{name:'Confirm local change',exact:true}).click();await page.getByText('The confirmed action succeeded.',{exact:true}).waitFor()
  await chat('أنشئ قسمًا اسمه Shopping');const create=page.getByRole('region',{name:'Action preview'}).last();await create.getByRole('checkbox').check();await create.getByRole('button',{name:'Confirm local change',exact:true}).click();await create.getByText('The confirmed action succeeded.',{exact:true}).waitFor()
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();await toggle().click();await nav().getByRole('button',{name:'Shopping',exact:true}).waitFor()
  await goto('/review?account=personal');await page.getByRole('button',{name:'Category Review',exact:true}).click();await page.getByLabel('Review scope',{exact:true}).selectOption('all')
  const unknown=page.locator('.v5-review-candidate').filter({hasText:'A plain note'})
  await unknown.getByRole('button',{name:'Mark reviewed',exact:true}).click();await unknown.waitFor({state:'detached'})
  const ambiguous=page.locator('.v5-review-candidate').filter({hasText:'Your order has shipped - discount sale'})
  await ambiguous.getByRole('combobox').selectOption('orders');await ambiguous.getByRole('button',{name:'Assign',exact:true}).click();await ambiguous.waitFor({state:'detached'})
  await page.reload();await page.getByRole('button',{name:'Category Review',exact:true}).click();await page.getByLabel('Review scope',{exact:true}).selectOption('all');assert.equal(await page.locator('.v5-review-candidate').count(),0)
  await page.getByRole('button',{name:'Decision history',exact:true}).click();await page.getByText('reviewed ·',{exact:false}).waitFor()
  await page.locator('main').getByRole('button',{name:'Spam',exact:true}).click();await page.getByRole('button',{name:'Review Restore…',exact:true}).waitFor()
  await page.locator('main').getByRole('button',{name:'Trash',exact:true}).click();await page.getByRole('button',{name:'Review Restore…',exact:true}).waitFor()
  await goto('/promotions?account=personal');await page.getByRole('complementary',{name:'Subscription management'}).waitFor();await page.getByRole('button',{name:'View all subscriptions',exact:true}).click()
  await page.getByText('offers.vendor.invalid',{exact:true}).waitFor();
  await page.locator('.v5-subscription-row').first().getByText('Technical Details',{exact:true}).click();await page.getByText('Advanced verification',{exact:true}).click();await page.getByRole('button',{name:'Check unsubscribe eligibility…',exact:true}).click()
  const unsubscribe=page.getByRole('region',{name:'Action preview'})
  await unsubscribe.getByText('Verified signed destination: vendor.invalid',{exact:true}).waitFor();assert.equal((await state()).unsubscribe_events.length,0)
  const confirm=unsubscribe.getByRole('button',{name:'Confirm selected unsubscribe requests',exact:true})
  assert.equal(await confirm.isDisabled(),true)
  await unsubscribe.getByRole('checkbox',{name:'I reviewed this exact action and account.'}).check();assert.equal(await confirm.isDisabled(),true)
  await unsubscribe.getByRole('checkbox',{name:'Approve unsubscribe from offers.vendor.invalid',exact:true}).check();await confirm.click()
  await unsubscribe.getByText('Approved requests were accepted; future delivery is not confirmed stopped.',{exact:true}).waitFor();assert.equal((await state()).unsubscribe_events.length,1)
  await unsubscribe.getByRole('button',{name:'Refresh result',exact:true}).click();assert.equal((await state()).unsubscribe_events.length,1)
  await page.screenshot({path:'/tmp/postmark-batch3_1-subscriptions.png',fullPage:true})
  await page.setViewportSize({width:390,height:844});await goto('/review?account=personal');await page.getByRole('button',{name:'Category Review',exact:true}).click();await page.getByLabel('Review scope',{exact:true}).selectOption('all');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:'/tmp/postmark-batch3_1-review-mobile.png',fullPage:true})
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.equal((await state()).paid_calls,0)
  console.log('PASS Batch 3.1 Chromium: grouped/persisted/revealed navigation, Arabic search/create/move, compact real messages, persisted classification review/assign/history, Spam/Trash tabs, Promotions summary navigation, real cryptographic fixture eligibility, exact-target approval, mocked one-click POST and replay safety, mobile layout. Zero real Gmail writes/AI/unsubscribe.')
} catch(error){if(page){console.error(await page.locator('main').innerText().catch(()=>''));await page.screenshot({path:'/tmp/postmark-batch3_1-failure.png',fullPage:true}).catch(()=>{})}throw error}
finally{await browser?.close();server.kill('SIGTERM')}
