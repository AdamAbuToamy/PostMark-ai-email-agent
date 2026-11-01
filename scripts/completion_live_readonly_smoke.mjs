// Local Settings-only acceptance. Reject every mutation and all external traffic.
import {chromium} from '../browser/node_modules/playwright/index.mjs'
import assert from 'node:assert/strict'
const browser=await chromium.launch({headless:true,chromiumSandbox:true})
try {
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'})
 const blocked=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/*',route=>{const request=route.request();if(request.method()!=='GET'){blocked.push(request.method());return route.abort()}if(!request.url().startsWith('http://127.0.0.1:8000/'))return route.abort();return route.continue()})
 await page.goto('http://127.0.0.1:8000/settings?account=personal')
 const ai=page.getByRole('region',{name:'AI assistant settings',exact:true})
 await ai.getByLabel('Active model',{exact:true}).waitFor()
 await ai.getByRole('button',{name:'Validate selected model',exact:true}).waitFor()
 assert.equal(await ai.getByRole('radio',{name:'Free only · recommended',exact:true}).isChecked(),true)
 assert.equal(await page.getByLabel('Shared daily request limit',{exact:true}).count(),0)
 assert.equal(await page.getByLabel('Verified free-tier RPD',{exact:true}).count(),0)
 await ai.screenshot({path:'docs/screenshots/completion-live-settings.png'})
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 await ai.screenshot({path:'docs/screenshots/completion-live-settings-mobile.png'})
 await page.reload();await ai.getByRole('button',{name:'Test connection',exact:true}).waitFor()
 assert.deepEqual(errors,[]);assert.deepEqual(blocked,[])
 console.log('PASS released completion Settings desktop/mobile/reload, saved Free-only, no legacy cap/RPD UI, zero non-GET requests, no runtime errors. No connection test or cancellation attempted.')
} finally {await browser.close()}
