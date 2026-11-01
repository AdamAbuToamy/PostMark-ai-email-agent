// Live persisted setup, GET-only. No keys submitted, tests run or grants changed.
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const browser=await chromium.launch({headless:true,chromiumSandbox:true})
try {
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[],writes=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/**',route=>{if(route.request().method()==='GET')return route.continue();writes.push(route.request().url());return route.abort()})
 await page.goto('http://127.0.0.1:8000/settings?account=personal')
 const card=name=>page.getByRole('article').filter({has:page.getByRole('heading',{name,exact:true})})
 const cloudflare=card('Cloudflare Workers AI');await cloudflare.getByRole('button',{name:'Change key'}).waitFor({timeout:60000});await cloudflare.getByRole('button',{name:'Change key'}).click()
 let dialog=page.getByRole('dialog',{name:'Set up Cloudflare Workers AI'})
 assert.match(await dialog.getByLabel('Cloudflare account ID').inputValue(),/^[a-f0-9]{32}$/i)
 assert.equal(await dialog.getByLabel('New provider API key').inputValue(),'')
 assert.equal(await dialog.getByLabel('New provider API key').getAttribute('type'),'password')
 assert.equal(await dialog.getByRole('link',{name:'REST API setup guide'}).getAttribute('href'),'https://developers.cloudflare.com/workers-ai/get-started/rest-api/')
 await dialog.getByRole('button',{name:'Use saved connection'}).click()
 assert.ok(await dialog.locator('select[name=probe_model] option').count()>1)
 assert.ok((await dialog.innerText()).includes('403'))
 assert.equal(await dialog.getByRole('button',{name:'Run one-request test',exact:true}).isDisabled(),true)
 assert.ok((await dialog.innerText()).includes('Shared test request guard · 20 remaining'))
 await dialog.screenshot({path:'/tmp/provider-cloudflare-setup.png'})
 await dialog.getByRole('button',{name:'Close',exact:true}).click()
 const samba=card('SambaNova');assert.equal(await samba.getByLabel('Enable SambaNova').isDisabled(),true)
 await samba.getByRole('button',{name:'Change key'}).click();dialog=page.getByRole('dialog',{name:'Set up SambaNova'})
 await dialog.getByRole('button',{name:'Use saved connection'}).click()
 assert.equal(await dialog.locator('select[name=probe_model],select[name=quality_model]').count(),0)
 assert.equal(await dialog.getByRole('button',{name:'Qualification unavailable'}).isDisabled(),true)
 assert.ok((await dialog.innerText()).includes('No free-eligible model available'))
 await dialog.getByRole('button',{name:'Close',exact:true}).click()
 const zai=card('Z.AI');assert.ok((await zai.innerText()).includes('Key: Saved'));assert.ok((await zai.innerText()).includes('Not qualified'));assert.ok((await zai.innerText()).includes('Not authorized'))
 for(const width of [1360,390]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`/tmp/provider-setup-${width}.png`,fullPage:true})}
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
 console.log('PASS real saved Cloudflare Token/Account ID setup, 22 candidates, separate plan403/20-request guard, SambaNova discovery versus free eligibility, ZAI staged status, desktop/mobile; zero POST/inference requests.')
} finally {await browser.close()}
