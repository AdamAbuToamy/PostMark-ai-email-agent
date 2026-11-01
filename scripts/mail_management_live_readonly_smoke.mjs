// Released local UI: GET-only requests, no Gmail/AI/backfill or remote actions.
import { chromium } from '../browser/node_modules/playwright/index.mjs'
import assert from 'node:assert/strict'
const browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
try {
 const base='http://127.0.0.1:8000'
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'})
 const blocked=[],errors=[]
 page.on('pageerror',e=>errors.push(e.message))
 page.on('response',r=>{if(r.url().includes('/api/mail-management/')&&r.status()>=400)errors.push('Mail management HTTP '+r.status())})
 await page.route('**/*',route=>{const req=route.request();if(req.method()!=='GET'){blocked.push(req.method());return route.abort()}if(!req.url().startsWith(base+'/'))return route.abort();return route.continue()})
 const get=async path=>{const response=await page.request.get(base+path);assert.equal(response.ok(),true);return response.json()}
 const policies={}
 const summary=[]
 for(const account of ['personal','university']){
  policies[account]=(await get('/api/cleanup/'+account)).policy
  const progress=await get('/api/mail-management/'+account+'/progress')
  assert.equal(progress.counts.remaining_local,0);assert.equal(progress.scan.status,'completed')
  for(const [path,view,title] of [['/cleanup-review','eligible','Cleanup Review'],['/cleanup-review','protected','Cleanup Review'],['/cleanup-review','history','Cleanup History'],['/trash','trash','Trash']]){
   await page.goto(base+path+'?account='+account+'&view='+view)
   const area=page.getByRole('region',{name:title,exact:true});await area.locator('.v5-mailbox-pagination').waitFor()
   const listing=await get('/api/mail-management/'+account+'/messages?view='+view+'&limit=25')
   assert.equal(await area.locator('.v5-managed-row').count(),listing.items.length)
   if(view==='protected')assert.ok(listing.total>0)
   if(view==='trash'&&account==='personal')assert.ok(listing.total>0)
   assert.equal(await area.getByLabel('Choose email account',{exact:true}).inputValue(),account)
  }
  const after=(await get('/api/cleanup/'+account)).policy
  assert.deepEqual(after,policies[account])
  summary.push({account,indexed:progress.counts.indexed,assessed:progress.counts.assessed,eligible:progress.counts.eligible,protected:progress.counts.protected,needs_review:progress.counts.needs_review,gmail_history_complete:!!progress.gmail_history.completed})
 }
 // Interactive switching intentionally expires local chat approvals (POST); tested in mocks only.
 for(const path of ['/cleanup-review?account=personal&view=protected','/trash?account=personal','/cleanup-review?account=university&view=history']){
  await page.setViewportSize({width:390,height:844});await page.goto(base+path)
  await page.locator('.v5-mail-management .v5-mailbox-pagination').waitFor()
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }
 assert.deepEqual(blocked,[]);assert.deepEqual(errors,[])
 console.log(JSON.stringify({result:'PASS released local Chromium GET-only',accounts:summary,non_get_operations:blocked.length,errors:errors.length,external_destinations:'blocked',gmail_mutations:'not executed',backfill:'not executed',gemini:'not called'}))
} finally {await browser.close()}
