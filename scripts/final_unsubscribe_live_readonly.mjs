// Released local subscription UI: no mutations and no external traffic.
import {chromium} from '../browser/node_modules/playwright/index.mjs'
import assert from 'node:assert/strict'
const browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'})
 const errors=[],blocked=[];page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/*',route=>{const req=route.request();if(req.method()!=='GET'){blocked.push(req.method());return route.abort()}if(!req.url().startsWith('http://127.0.0.1:8000/'))return route.abort();return route.continue()})
 await page.goto('http://127.0.0.1:8000/subscriptions?account=personal')
 const row=page.locator('.v5-subscription-row').first();await row.getByRole('button',{name:'Unsubscribe',exact:true}).waitFor()
 assert.equal(await row.locator('.v5-subscription-row-actions button').count(),2)
 assert.equal(await row.getByRole('button',{name:'Open unsubscribe page myself',exact:true}).isVisible(),true)
 assert.equal(await row.getByRole('button',{name:'Open original message',exact:true}).isVisible(),false)
 assert.equal(await row.locator('details[open]').count(),0)
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 await page.reload();await row.getByRole('button',{name:'Unsubscribe',exact:true}).waitFor()
 assert.deepEqual(errors,[]);assert.deepEqual(blocked,[])
 console.log('PASS released subscription UI desktop/mobile/reload: two compact actions, hidden diagnostics, zero non-GET or external traffic. No cancellation, manual page, Gmail or Gemini operation initiated.')
}finally{await browser.close()}
