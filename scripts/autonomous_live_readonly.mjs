// Published progress only. No body fetches, changes or provider requests.
import {readFileSync} from 'node:fs'
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const output=readFileSync('/tmp/autonomous-release.txt','utf8').trim(),base='http://127.0.0.1:8000'
const browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[],writes=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',route=>{const r=route.request();if(r.method()!=='GET'||r.url().includes('/body')){writes.push(r.method());return route.abort()}return r.url().startsWith(base+'/')?route.continue():route.abort()})
 for(const account of ['personal','university']){
  const response=await page.request.get(base+'/api/organizer/'+account);assert.equal(response.status(),200);const data=await response.json()
  assert.equal(data.automation.enabled,true);assert.ok(data.automation.organized<=data.automation.bodies_complete);assert.equal(data.automation.ai.authorized,account==='personal')
  for(const width of [1360,390]){
   await page.setViewportSize({width,height:1000});await page.goto(base+'/categories?account='+account)
   const panel=page.getByLabel('Semantic organizer',{exact:true});await panel.getByRole('status').filter({hasText:/Organizing|Needs attention|Up to date/}).waitFor()
   await panel.getByText('Advanced organization',{exact:true}).click();await panel.getByText('Background queues and consent budgets',{exact:true}).click()
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   await page.addStyleTag({content:'.v5-organization-group ul,.pm-group-preview,.v5-message-data{filter:blur(7px)}'})
   await page.screenshot({path:output+`/automation-${account}-${width}.png`,fullPage:true})
  }
 }
 const university=await page.request.get(base+'/api/chat/university/settings').then(r=>r.json());assert.equal(university.enabled,false);assert.equal(university.processing_consent,false)
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
 console.log('PASS published account-specific background status, full-body counts, Advanced consent/queue details, desktop/mobile bounds and University consent unchanged. Browser made GET requests only; no Gmail/model/body/action operations.')
}finally{await browser.close()}
