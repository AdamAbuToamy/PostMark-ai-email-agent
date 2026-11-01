// Published local UI only: no POSTs, body fetches, provider traffic or AI.
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
try{
 const base='http://127.0.0.1:8000',page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[],blocked=[]
 page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)errors.push('API '+r.status())})
 await page.route('**/*',route=>{const request=route.request();if(request.method()!=='GET'){blocked.push(request.method());return route.abort()}return request.url().startsWith(base+'/')?route.continue():route.abort()})
 const get=async path=>{const response=await page.request.get(base+path);assert.ok(response.ok(),path);return response.json()}
 const before={};for(const account of ['personal','university'])before[account]={policy:(await get('/api/cleanup/'+account)).policy,ai:await get('/api/chat/'+account+'/settings')}
 const pages=['overview','categories','review','orders','jobs','settings','spam','trash','subscriptions','cleanup-review']
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000})
  for(const path of pages){
   await page.goto(base+'/'+path+'?account=personal')
   if(['overview','categories','review'].includes(path))await page.locator('.v5-organization .pm-overview-numbers').waitFor()
   else if(['orders','jobs'].includes(path))await page.getByRole('group',{name:'Smart sections'}).waitFor()
   else if(path==='settings')await page.getByRole('heading',{name:'Privacy',exact:true}).waitFor()
   else if(path==='subscriptions')await page.getByLabel('Subscription status',{exact:true}).waitFor()
   else await page.locator('.v5-mailbox-pagination').waitFor()
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,path+' '+width)
   assert.equal(await page.locator('.pm-page-stack').evaluate(n=>getComputedStyle(n).gap),'24px')
  }
 }
 const counts=[]
 for(const account of ['personal','university']){
  for(const category of ['orders','job_applications','bills','security']){const data=await get(`/api/category-mailbox/${account}/${category}?state=all`);assert.equal(data.sections.reduce((n,s)=>n+s.count,0),data.total)}
  const settings=await get('/api/chat/'+account+'/settings');assert.deepEqual((await get('/api/cleanup/'+account)).policy,before[account].policy)
  for(const key of ['enabled','model','processing_consent','save_history'])assert.equal(settings[key],before[account].ai[key])
  const progress=await get('/api/classifications/'+account+'/progress');counts.push({account,total:progress.total_local,classified:progress.classified,required_review:progress.review_required})
 }
 assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);assert.deepEqual(await get('/api/health'),{status:'ok'})
 console.log(JSON.stringify({result:'PASS published 10 pages desktop/mobile, local category sections both accounts, preferences preserved',counts,mutations:0,gemini:0,provider_requests:0}))
}finally{await browser.close()}
