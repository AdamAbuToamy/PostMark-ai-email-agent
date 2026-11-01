import {openAIPreferences} from './ui_smoke_helpers.mjs'
import { spawn } from 'node:child_process'
import { setTimeout as pause } from 'node:timers/promises'
import { pathToFileURL } from 'node:url'
import { mkdir } from 'node:fs/promises'
import assert from 'node:assert/strict'
const {chromium}=await import(pathToFileURL(process.argv[2]??'./browser/node_modules/playwright/index.mjs').href)
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',BATCH3_1_FIXTURE:'1',BATCH3_2_FIXTURE:'1',BATCH3_3_FIXTURE:'1',BATCH3_4_FIXTURE:'1',BATCH3_5_FIXTURE:'1',CORE_FEATURE_FIXTURE:'1',COMPLETION_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser,page
try {
 const base='http://127.0.0.1:8012',shots='./docs/screenshots'
 await mkdir(shots,{recursive:true})
 for(let i=0;i<60;i++){if(server.exitCode!==null)throw Error('Fixture exited');try{if((await fetch(base+'/api/health')).ok)break}catch{}if(i===59)throw Error('Fixture unavailable');await pause(200)}
 browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
 page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(10000)
 const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.route('https://fonts.googleapis.com/**',r=>r.abort())
 const headers={'X-Email-Agent-Action':'message-management','Origin':'http://127.0.0.1:8000'}
 const get=path=>page.request.get(base+path).then(r=>r.json())
 const goto=async path=>{await page.goto(base+path);if(path.startsWith('/settings'))await openAIPreferences(page)}
 const state=()=>get('/fixture/state')
 const ai=()=>page.getByRole('region',{name:'AI assistant settings',exact:true})
 const dialog=()=>page.getByRole('dialog',{name:'Confirm action',exact:true})
 const row=name=>page.locator('.v5-subscription-row').filter({has:page.getByText(name+'.website.example',{exact:true})})
 const panel=()=>page.getByRole('region',{name:'Unsubscribe',exact:true})
 async function noOverflow(){assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)}
 await goto('/settings?account=personal');await ai().getByRole('button',{name:'Test connection',exact:true}).waitFor()
 assert.equal(await ai().getByLabel('Active model',{exact:true}).isVisible(),false)
 assert.equal(await ai().getByRole('button',{name:'Validate selected model',exact:true}).isVisible(),false)
 assert.equal(await ai().getByRole('button',{name:'Delete all chat history',exact:true}).isVisible(),false)
 assert.equal(await ai().getByRole('radio',{name:'Paid',exact:true}).evaluate(el=>getComputedStyle(el).appearance),'none')
 assert.equal(await ai().getByRole('checkbox',{name:'AI on',exact:true}).evaluate(el=>getComputedStyle(el).appearance),'none')
 assert.equal(await ai().getByRole('checkbox',{name:'AI on',exact:true}).evaluate(el=>getComputedStyle(el,'::after').width),'14px');await ai().screenshot({path:shots+'/ui-polish-settings-desktop.png'})
 await ai().getByRole('button',{name:'Test connection',exact:true}).click();await dialog().waitFor()
 await dialog().getByRole('button',{name:'Cancel',exact:true}).waitFor();assert.equal(await dialog().evaluate(node=>node.contains(document.activeElement)),true)
 await dialog().screenshot({path:shots+'/ui-polish-confirmation.png'});await page.keyboard.press('Escape');assert.equal((await state()).connection_calls,0)
 assert.equal(await ai().getByRole('button',{name:'Test connection',exact:true}).evaluate(el=>el===document.activeElement),true)
 // A removed saved choice is retained, diagnosed, and switched only after confirmation.
 await page.request.post(base+'/api/chat/personal/model',{headers,data:{model:'gemini-3.8-flash',approved:true}})
 await page.reload();await ai().getByRole('button',{name:'Switch to Gemini 3.5 Flash Lite',exact:true}).click();await dialog().getByRole('button',{name:'Cancel',exact:true}).click()
 assert.equal((await get('/api/chat/personal/settings')).model,'gemini-3.8-flash')
 await ai().getByText('Advanced Settings',{exact:true}).click();assert.equal(await ai().locator('option[value="gemini-3.8-flash"]').count(),0)
 await ai().getByRole('button',{name:'Switch to Gemini 3.5 Flash Lite',exact:true}).click();await dialog().getByRole('button',{name:'Confirm',exact:true}).click();await ai().getByText('Model changed. No request was sent.',{exact:true}).waitFor()
 assert.equal((await get('/api/chat/personal/settings')).model,'gemini-3.5-flash-lite');assert.equal((await state()).model_calls,0)
 await goto('/chat?account=personal');await page.getByRole('button',{name:'New chat',exact:true}).waitFor()
 assert.equal(await page.getByRole('button',{name:'Clear all history',exact:true}).isVisible(),false)
 assert.equal(await page.getByRole('button',{name:'Open AI Settings',exact:true}).locator('..').getAttribute('class'),'v5-chat-toolbar')
 await page.getByLabel('Message',{exact:true}).fill('اعطني ميلات الي اكسبرس');await page.getByRole('button',{name:'Send',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#chat-message')?.value==='')
 assert.equal((await state()).model_calls,0);assert.equal(await page.locator('.v5-processor').count(),0)
 await page.screenshot({path:shots+'/ui-polish-chat-desktop.png',fullPage:true})
 await page.getByRole('button',{name:'Clear conversation',exact:true}).click();await dialog().getByRole('button',{name:'Cancel',exact:true}).click();assert.ok((await get('/api/chat/personal/history')).length>0)
 await page.getByRole('button',{name:'Clear conversation',exact:true}).click();await dialog().getByRole('button',{name:'Confirm',exact:true}).click();await page.getByText('What would you like to organize?',{exact:true}).waitFor()
 await goto('/subscriptions?account=personal');await row('or-direct').getByRole('button',{name:'Unsubscribe',exact:true}).waitFor()
 assert.equal(await page.getByRole('progressbar',{name:'Metadata discovery progress'}).isVisible(),false)
 assert.equal(await page.getByLabel('Subscription status',{exact:true}).evaluate(el=>getComputedStyle(el).appearance),'none')
 await page.screenshot({path:shots+'/ui-polish-subscriptions-overview.png',fullPage:false});await page.getByLabel('Search subscriptions',{exact:true}).fill('or-direct');assert.equal(await page.locator('.v5-subscription-row').count(),1)
 await page.screenshot({path:shots+'/ui-polish-subscriptions-desktop.png',fullPage:true})
 await row('or-direct').getByRole('button',{name:'Unsubscribe',exact:true}).click();await panel().getByRole('button',{name:'Unsubscribe',exact:true}).click();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor()
 const posts=(await state()).website_events.filter(e=>e.method==='POST').length
 await panel().getByRole('button',{name:'Dismiss unsubscribe',exact:true}).click();await row('or-direct').getByText('Unsubscribed',{exact:true}).waitFor();await page.getByLabel('Subscription status',{exact:true}).selectOption('active');await page.getByText('No subscriptions here.',{exact:true}).waitFor()
 await page.getByLabel('Subscription status',{exact:true}).selectOption('unsubscribed');await row('or-direct').getByText('Unsubscribed',{exact:true}).waitFor()
 await page.screenshot({path:shots+'/ui-polish-subscriptions-unsubscribed.png',fullPage:false});await row('or-direct').getByRole('button',{name:'View status',exact:true}).click();await panel().getByRole('heading',{name:'Unsubscribed',exact:true}).waitFor()
 assert.equal((await state()).website_events.filter(e=>e.method==='POST').length,posts)
 await panel().getByRole('button',{name:'Dismiss unsubscribe',exact:true}).click();await page.reload();await row('or-direct').getByText('Unsubscribed',{exact:true}).waitFor()
 await page.getByLabel('Subscription status',{exact:true}).selectOption('unsubscribed');await row('or-direct').getByText('Unsubscribed',{exact:true}).waitFor()
 await page.getByLabel('Choose email account',{exact:true}).selectOption('university');await page.waitForURL('**account=university');assert.equal(await row('or-direct').count(),0)
 await page.setViewportSize({width:390,height:844})
 for(const [path,name] of [['/settings?account=personal','settings'],['/chat?account=personal','chat'],['/subscriptions?account=personal','subscriptions'],['/promotions?account=personal','promotions']]){
  await goto(path);await page.locator('main').waitFor();if(name==='settings')await ai().getByRole('button',{name:'Test connection',exact:true}).waitFor();else if(name==='subscriptions')await page.getByLabel('Search subscriptions',{exact:true}).waitFor();else if(name==='chat')await page.getByLabel('Message',{exact:true}).waitFor();else await page.locator('.v5-subscription-summary-row').first().waitFor()
  await noOverflow();await page.screenshot({path:shots+'/ui-polish-'+name+'-mobile.png',fullPage:true})
 }
 await page.setViewportSize({width:1360,height:1000});await goto('/promotions?account=personal');await page.locator('.v5-subscription-summary-row').first().waitFor();await page.screenshot({path:shots+'/ui-polish-promotions-desktop.png',fullPage:true});
 assert.equal((await state()).model_calls,0);assert.equal((await state()).connection_calls,0);assert.equal((await state()).events.length,0);assert.equal((await state()).paid_calls,0);assert.deepEqual(errors,[])
 console.log('PASS UI polish Chromium: styled controls/keyboard focus/dialog cancel and confirm; compact default settings/advanced; removed saved model explicit recovery without requests; chat local Arabic and clear; persistent cancellation tabs/search/account isolation/no replay; desktop/mobile Settings Chat Subscriptions Promotions. Mocked services only.')
} catch(error){if(page){console.error(await page.locator('main').innerText().catch(()=>''));await page.screenshot({path:'/tmp/ui-polish-failure.png',fullPage:true}).catch(()=>{})}throw error} finally {await browser?.close();server.kill('SIGTERM')}
