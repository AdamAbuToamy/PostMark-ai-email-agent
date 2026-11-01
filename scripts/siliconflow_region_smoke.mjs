// Persisted region and saved-secret UX, GET-only. No provider/inference requests.
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[],writes=[]
 page.on('pageerror',e=>errors.push(e.message));await page.route('**/api/**',route=>route.request().method()==='GET'?route.continue():(writes.push(route.request().url()),route.abort()))
 await page.goto('http://127.0.0.1:8000/settings?account=personal')
 const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:'SiliconFlow',exact:true})});await card.getByRole('button',{name:'Change key'}).waitFor({timeout:60000});await card.getByRole('button',{name:'Change key'}).click()
 const dialog=page.getByRole('dialog',{name:'SiliconFlow setup'}),region=dialog.getByLabel('SiliconFlow account region'),secret=dialog.getByLabel('New provider API key')
 assert.equal(await region.inputValue(),'international');assert.equal(await secret.inputValue(),'');assert.equal(await secret.getAttribute('type'),'password');assert.equal(await secret.getAttribute('required'),null)
 assert.equal(await dialog.getByRole('link',{name:'Create API key'}).getAttribute('href'),'https://cloud.siliconflow.com/account/ak')
 await region.selectOption('china');assert.equal(await dialog.getByRole('link',{name:'Create API key'}).getAttribute('href'),'https://cloud.siliconflow.cn/account/ak');assert.deepEqual(writes,[])
 await dialog.getByRole('button',{name:'Close',exact:true}).click();await page.reload();await card.getByRole('button',{name:'Change key'}).click();assert.equal(await page.getByLabel('SiliconFlow account region').inputValue(),'international')
 for(const width of [1360,390]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`/tmp/siliconflow-region-${width}.png`,fullPage:true})}
 assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);console.log('PASS persisted explicit International region, China choice without saving/fallback, regional key links, saved key reusable without display/re-entry, desktop/mobile; GET-only.')
}finally{await browser.close()}
