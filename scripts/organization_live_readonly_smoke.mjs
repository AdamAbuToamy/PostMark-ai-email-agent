// Production UI verification: read-only local endpoints; no external destinations.
import {chromium} from '../browser/node_modules/playwright/index.mjs'
import assert from 'node:assert/strict'
const browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
try {
 const base='http://127.0.0.1:8000',page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'})
 const errors=[],blocked=[]
 page.on('pageerror',e=>errors.push(e.message))
 page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)errors.push('Local API HTTP '+r.status())})
 await page.route('**/*',route=>{const request=route.request();if(request.method()!=='GET'){blocked.push(request.method());return route.abort()}return request.url().startsWith(base+'/')?route.continue():route.abort()})
 const report=[]
 for(const account of ['personal','university']) {
  const response=await page.request.get(base+`/api/organization/${account}`);assert.equal(response.ok(),true)
  const data=await response.json();assert.ok(data.summary.organized>0);assert.ok(data.total>0)
  assert.equal(data.summary.truncated,false)
  for(const path of ['categories','review','settings','']) {
   await page.goto(`${base}/${path}?account=${account}`)
   if(path==='settings'){for(const name of ['AI Assistant','Automatic Cleanup','Accounts','Privacy'])await page.getByRole('heading',{name,exact:true}).waitFor()}
   else {await page.getByRole('region',{name:'Mail organization',exact:true}).locator('.pm-overview-numbers').waitFor()}
  }
  report.push({account,indexed:data.summary.indexed,organized:data.summary.organized,recurring_groups:data.total})
 }
 await page.setViewportSize({width:390,height:844})
 for(const path of ['categories','review','settings']) {
  await page.goto(`${base}/${path}?account=personal`)
  if(path==='settings')await page.getByRole('heading',{name:'Privacy',exact:true}).waitFor()
  else await page.getByRole('region',{name:'Mail organization',exact:true}).locator('.pm-overview-numbers').waitFor()
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }
 assert.deepEqual(errors,[]);assert.deepEqual(blocked,[])
 console.log(JSON.stringify({result:'PASS production organization GET-only',accounts:report,non_get:0,external_destinations:'blocked',gmail_mutations:0,gemini_requests:0}))
} finally {await browser.close()}
