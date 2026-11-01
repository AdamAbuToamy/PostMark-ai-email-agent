// Real provider setup state only: never submit keys, grants or synthetic requests.
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const browser=await chromium.launch({headless:true,chromiumSandbox:true})
try {
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[],writes=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/**',route=>route.request().method()==='GET'?route.continue():(writes.push(route.request().url()),route.abort()))
 await page.goto('http://127.0.0.1:8000/settings?account=personal')
 const card=name=>page.getByRole('article').filter({has:page.getByRole('heading',{name,exact:true})})
 const cf=card('Cloudflare Workers AI');await cf.getByText('Needs free-tier verification',{exact:true}).waitFor({timeout:60000});assert.match(await cf.innerText(),/Account-scoped Billing Read/)
 const zai=card('Z.AI');assert.match(await zai.innerText(),/Needs model qualification|Needs account permission/)
 await zai.getByRole('button',{name:'Change key'}).click();const dialog=page.getByRole('dialog',{name:'Set up Z.AI'})
 assert.equal(await dialog.getByLabel('New provider API key').inputValue(),'');await dialog.getByRole('button',{name:'Use saved connection'}).click()
 const choices=await dialog.locator('select[name=quality_model] option').allTextContents();assert.ok(choices.includes('glm-4.7-flash'));assert.ok(!choices.includes('glm-5.3-flash'))
 await dialog.getByRole('button',{name:'Close',exact:true}).click()
 for(const width of [1360,390]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`/tmp/zai-route-settings-${width}.png`,fullPage:true})}
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
 console.log('PASS live provider next-action states, Cloudflare account verification blocker, ZAI free model choices, secret masking, desktop/mobile; GET-only.')
} finally {await browser.close()}
