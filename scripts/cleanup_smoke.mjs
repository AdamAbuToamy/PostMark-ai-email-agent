import { spawn } from 'node:child_process'
import { setTimeout as pause } from 'node:timers/promises'
import { pathToFileURL } from 'node:url'
import { mkdir } from 'node:fs/promises'
import assert from 'node:assert/strict'
const {chromium}=await import(pathToFileURL(process.argv[2]??'./browser/node_modules/playwright/index.mjs').href)
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',BATCH3_1_FIXTURE:'1',BATCH3_2_FIXTURE:'1',BATCH3_3_FIXTURE:'1',BATCH3_4_FIXTURE:'1',BATCH3_5_FIXTURE:'1',CORE_FEATURE_FIXTURE:'1',COMPLETION_FIXTURE:'1',CLEANUP_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser,page
try {
 const base='http://127.0.0.1:8012',shots='./docs/screenshots';await mkdir(shots,{recursive:true})
 for(let i=0;i<60;i++){if(server.exitCode!==null)throw Error('Fixture exited');try{if((await fetch(base+'/api/health')).ok)break}catch{}if(i===59)throw Error('Fixture unavailable');await pause(200)}
 browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
 page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(10000)
 const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.route('https://fonts.googleapis.com/**',r=>r.abort())
 const area=()=>page.getByRole('region',{name:'Automatic Cleanup',exact:true})
 const dialog=()=>page.getByRole('dialog',{name:'Confirm action',exact:true})
 const get=path=>page.request.get(base+path).then(r=>r.json())
 const state=()=>get('/fixture/state')
 const data=()=>get('/api/cleanup/personal')
 const fixture=async payload=>{const response=await page.request.post(base+'/fixture/cleanup',{data:payload});assert.equal(response.status(),200)}
 const receive=(id,kind='ad',account='personal')=>fixture({operation:'receive',message_id:id,kind,account})
 const tick=()=>fixture({operation:'tick'})
 async function refresh(){const response=page.waitForResponse(r=>r.url()===base+'/api/cleanup/personal'&&r.request().method()==='GET');await area().getByRole('button',{name:'Refresh cleanup history',exact:true}).click();await response}
 await page.goto(base+'/settings?account=personal');await area().getByRole('button',{name:'Save cleanup preferences',exact:true}).waitFor();await area().getByText('Advanced cleanup',{exact:true}).click()
 assert.equal((await data()).policy.enabled,false);assert.equal((await data()).policy.mode,'review');assert.equal((await state()).events.length,0)
 await area().screenshot({path:shots+'/cleanup-off-desktop.png'})
 // Historical events are outside the policy watermark even if they look promotional.
 await receive('cleanup-before')
 await area().getByRole('checkbox',{name:'Enable automatic promotional cleanup',exact:true}).check()
 await area().getByRole('button',{name:'Save cleanup preferences',exact:true}).click();await area().getByText('Review only enabled. Gmail will not be changed.',{exact:true}).waitFor()
 await receive('cleanup-review');await tick();assert.equal((await state()).events.length,0)
 await area().getByText('Review recent actions',{exact:true}).click();await refresh();await area().getByText(/Ready for review/).waitFor()
 await area().getByRole('button',{name:/^Cleanup fixture discount sale cleanup-review/}).click();await page.getByRole('dialog',{name:'Cleanup fixture discount sale cleanup-review',exact:true}).waitFor();await page.getByRole('button',{name:'Close details',exact:true}).click()
 // Cancel never enables automatic mode. Confirm grants one future policy, not a batch.
 await area().getByRole('radio',{name:'Move ads to Trash',exact:true}).check();await area().getByRole('button',{name:'Save cleanup preferences',exact:true}).click();await dialog().getByRole('button',{name:'Cancel',exact:true}).click();assert.equal((await data()).policy.mode,'review')
 await area().getByRole('button',{name:'Save cleanup preferences',exact:true}).click();await dialog().screenshot({path:shots+'/cleanup-authorization.png'});await dialog().getByRole('button',{name:'Confirm',exact:true}).click();await area().getByText('Automatic cleanup enabled for future eligible ads.',{exact:true}).waitFor()
 await receive('cleanup-ad');await receive('cleanup-order','order');await tick()
 assert.deepEqual((await state()).events.map(e=>[e.operation,e.message_id]),[['trash','cleanup-ad']])
 await tick();assert.equal((await state()).events.length,1)
 await refresh();await area().getByText(/Cleaned today: 1/).waitFor()
 const row=id=>area().locator('.v5-cleanup-history').filter({has:page.getByText('Cleanup fixture discount sale '+id,{exact:true})})
 await row('cleanup-ad').getByRole('button',{name:'Restore',exact:true}).waitFor()
 await area().screenshot({path:shots+'/cleanup-history-desktop.png'})
 await row('cleanup-ad').getByRole('button',{name:'Restore',exact:true}).click();const approval=area().getByRole('region',{name:'Action preview',exact:true});await approval.getByRole('checkbox',{name:'I reviewed this exact action and account.',exact:true}).check();await approval.getByRole('button',{name:'Confirm Restore',exact:true}).click();await approval.getByText('The confirmed action succeeded.',{exact:true}).waitFor();assert.equal((await state()).events.at(-1).operation,'restore')
 await row('cleanup-ad').getByRole('button',{name:'Keep this message',exact:true}).click();await area().getByText('Message protected from future cleanup.',{exact:true}).waitFor();await area().getByText('Protected senders',{exact:true}).click();await area().getByLabel('Protected cleanup senders',{exact:true}).fill('news@cleanup.example');await area().getByRole('button',{name:'Save cleanup preferences',exact:true}).click();await dialog().getByRole('button',{name:'Confirm',exact:true}).click();await area().getByText('Automatic cleanup enabled for future eligible ads.',{exact:true}).waitFor();await receive('cleanup-protected');await tick();assert.equal((await state()).events.length,2)
 // Account switching cannot carry authorization or history to a readonly account.
 await page.getByRole('article',{name:'university account sync',exact:true}).getByRole('button',{name:'Use account',exact:true}).click();await page.waitForURL('**account=university');await area().getByRole('button',{name:'Save cleanup preferences',exact:true}).waitFor();await area().getByText('Advanced cleanup',{exact:true}).click();assert.equal(await area().getByRole('checkbox',{name:'Enable automatic promotional cleanup',exact:true}).isChecked(),false);assert.equal(await area().getByRole('radio',{name:'Move ads to Trash',exact:true}).isDisabled(),true)
 await page.getByRole('article',{name:'personal account sync',exact:true}).getByRole('button',{name:'Use account',exact:true}).click();await page.waitForURL('**account=personal');await area().getByRole('checkbox',{name:'Enable automatic promotional cleanup',exact:true}).waitFor();await area().getByText('Advanced cleanup',{exact:true}).click();assert.equal(await area().getByRole('checkbox',{name:'Enable automatic promotional cleanup',exact:true}).isChecked(),true)
 await area().getByText('Protected senders',{exact:true}).click();await area().getByLabel('Protected cleanup senders',{exact:true}).fill('invalid unsaved address');await area().getByRole('checkbox',{name:'Enable automatic promotional cleanup',exact:true}).uncheck();await area().getByText('Automatic cleanup is off.',{exact:true}).waitFor();assert.equal((await data()).policy.enabled,false)
 await receive('cleanup-disabled');await tick();assert.equal((await state()).events.length,2)
 await page.reload();await area().getByText('Advanced cleanup',{exact:true}).click();await area().getByRole('checkbox',{name:'Enable automatic promotional cleanup',exact:true}).waitFor();assert.equal(await area().getByRole('checkbox',{name:'Enable automatic promotional cleanup',exact:true}).isChecked(),false)
 // Unknown acknowledgement pauses, and a read-only check resolves without a POST repeat.
 await area().getByText('Protected senders',{exact:true}).click();await area().getByLabel('Protected cleanup senders',{exact:true}).fill('');await area().getByRole('checkbox',{name:'Enable automatic promotional cleanup',exact:true}).check();await area().getByRole('button',{name:'Save cleanup preferences',exact:true}).click();await dialog().getByRole('button',{name:'Confirm',exact:true}).click();await area().getByText('Automatic cleanup enabled for future eligible ads.',{exact:true}).waitFor()
 await receive('cleanup-uncertain');await tick();assert.equal((await data()).policy.enabled,false)
 await area().getByText('Review recent actions',{exact:true}).click();await refresh();await area().getByText(/Needs verification/).waitFor();const writes=(await state()).events.length
 await row('cleanup-uncertain').getByRole('button',{name:'Check status',exact:true}).click();await area().getByText('Status checked. No message was moved.',{exact:true}).waitFor();assert.equal((await state()).events.length,writes);assert.equal((await data()).history.items.find(i=>i.message_id==='cleanup-uncertain').status,'succeeded')
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await area().screenshot({path:shots+'/cleanup-mobile.png'})
 assert.equal((await state()).model_calls,0);assert.equal((await state()).connection_calls,0);assert.equal((await state()).unsubscribe_events.length,0);assert.deepEqual(errors,[])
 console.log('PASS cleanup Chromium: disabled default/review; exact setup Cancel/Confirm; future ads only; protected orders/history; no repeat on sync; real message navigation; existing Restore approval; sender protection; account isolation/readonly; immediate off/reload; uncertain result pause and read-only reconciliation; desktop/mobile. Gmail/Gemini/sites mocked.')
} catch(error){if(page){console.error(await page.locator('main').innerText().catch(()=>''));await page.screenshot({path:'/tmp/cleanup-smoke-failure.png',fullPage:true}).catch(()=>{})}throw error}finally{await browser?.close();server.kill('SIGTERM')}
