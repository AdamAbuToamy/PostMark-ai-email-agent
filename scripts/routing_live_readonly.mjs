// Production verification: GET only. No provider test, settings write, or Gmail body read.
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',out=readFileSync('/tmp/routing-checkpoint.txt','utf8').trim()
const browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1100},reducedMotion:'reduce'});page.setDefaultTimeout(45000)
 const errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',r=>r.request().method()==='GET'&&r.request().url().startsWith(base+'/')&&!r.request().url().includes('/body')?r.continue():r.abort())
 for(const account of ['personal','university']){
  await page.goto(`${base}/categories?account=${account}`)
  const progress=page.getByLabel('Organization worker progress',{exact:true});await progress.waitFor()
  await progress.getByText(/AI completed/).waitFor()
  await page.locator('.pm-overview-numbers').waitFor()
  assert.equal(await page.getByText('Finding groups…',{exact:true}).count(),0)
  await progress.getByText('Why models are waiting',{exact:true}).click()
  await progress.getByText('gemini',{exact:true}).waitFor()
  assert.match(await progress.innerText(),/legacy budget cannot fit next complete request|Previous Gemini scope/)
  for(const width of [1360,390]){
   await page.setViewportSize({width,height:1100})
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   await page.screenshot({path:`${out}/groups-${account}-${width}.png`,fullPage:true})
  }
  const started=performance.now();const response=await page.request.get(`${base}/api/organization/${account}?limit=12&state=all`);assert.equal(response.status(),200)
  const data=await response.json();assert.ok(data.summary.indexed>0)
  console.log(account,'repeat_preview_seconds',((performance.now()-started)/1000).toFixed(3),'indexed',data.summary.indexed)
 }
 assert.deepEqual(errors,[])
 console.log('PASS deployed actual worker progress, model exclusion reasons, finished group preview, account isolation, desktop/mobile overflow. GET-only.')
}finally{await browser.close()}
