// Released local UI only: block every non-GET and all external traffic.
import { chromium } from '../browser/node_modules/playwright/index.mjs'
import assert from 'node:assert/strict'
const browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
try {
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'})
 const blocked=[],errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',route=>{const req=route.request();if(req.method()!=='GET'){blocked.push(req.method());return route.abort()}if(!req.url().startsWith('http://127.0.0.1:8000/'))return route.abort();return route.continue()})
 const area=()=>page.getByRole('region',{name:'Automatic Cleanup',exact:true})
 for(const account of ['personal','university']){
  await page.goto('http://127.0.0.1:8000/settings?account='+account)
  const toggle=area().getByRole('checkbox',{name:'Enable automatic promotional cleanup',exact:true});await toggle.waitFor()
  assert.equal(await toggle.isChecked(),false)
  assert.equal(await area().getByRole('radio',{name:'Review only',exact:true}).isChecked(),true)
  if(account==='personal')await area().screenshot({path:'./docs/screenshots/cleanup-released-off.png'})
 }
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 await page.goto('http://127.0.0.1:8000/subscriptions?account=personal');await page.getByLabel('Search subscriptions',{exact:true}).waitFor();await page.getByLabel('Subscription status',{exact:true}).selectOption('all');await page.reload();await page.getByLabel('Subscription status',{exact:true}).waitFor()
 assert.deepEqual(blocked,[]);assert.deepEqual(errors,[])
 console.log('PASS released local Chromium read-only: cleanup Off/Review for both accounts, mobile layout, Subscriptions filter/reload. Zero non-GET operations and no external destinations opened. No Gmail/AI/unsubscribe operation initiated.')
}finally{await browser.close()}
