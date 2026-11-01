// Published UI: local GETs only. No message bodies, mutation, history sync or AI.
import {chromium} from '../browser/node_modules/playwright/index.mjs'
import assert from 'node:assert/strict'
const browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
try{
 const base='http://127.0.0.1:8000',page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'})
 const errors=[],blocked=[];page.on('pageerror',()=>errors.push('page error'));page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)errors.push(new URL(r.url()).pathname+' '+r.status())})
 await page.route('**/*',route=>{const request=route.request();if(request.method()!=='GET'){blocked.push(request.method());return route.abort()}return request.url().startsWith(base+'/')?route.continue():route.abort()})
 const get=async path=>{const r=await page.request.get(base+path);assert.equal(r.ok(),true,path);return r.json()}
 const facts=[]
 for(const account of ['personal','university']){
  const policy=(await get('/api/cleanup/'+account)).policy
  const settings=await get('/api/chat/'+account+'/settings')
  const classification=await get('/api/classifications/'+account+'/progress')
  await page.goto(base+'/settings?account='+account)
  await page.getByRole('heading',{name:'Privacy',exact:true}).waitFor()
  assert.equal(await page.getByRole('region',{name:'Automatic Cleanup',exact:true}).count(),1)
  assert.equal(await page.locator('.pm-global-advanced').evaluate(n=>n.open),false)
  assert.equal(await page.getByRole('navigation',{name:'Primary navigation',exact:true}).count(),0)
  for(const id of ['personal','university'])await page.getByRole('article',{name:id+' account sync',exact:true}).getByRole('button',{name:'Sync all old mail',exact:true}).waitFor()
  for(const path of ['orders','categories','review','subscriptions','spam','trash','cleanup-review']){
   await page.goto(base+'/'+path+'?account='+account)
   if(path==='orders')await page.getByRole('group',{name:'Smart sections',exact:true}).waitFor()
   else if(path==='categories'||path==='review')await page.getByRole('region',{name:'Mail organization',exact:true}).locator('.v5-mailbox-pagination').waitFor()
   else if(['spam','trash','cleanup-review'].includes(path))await page.locator('.v5-mailbox-pagination').waitFor()
   else await page.getByLabel('Subscription status',{exact:true}).waitFor()
  }
  const orders=await get(`/api/category-mailbox/${account}/orders?state=all`)
  assert.equal(orders.sections.reduce((n,s)=>n+s.count,0),orders.total)
  if(orders.sections.some(s=>s.count)){const section=orders.sections.find(s=>s.count);const result=await get(`/api/category-mailbox/${account}/orders?state=all&section=${section.id}`);assert.equal(result.total,section.count);assert.ok(result.items.every(i=>i.section_id===section.id))}
  assert.deepEqual((await get('/api/cleanup/'+account)).policy,policy)
  const after=await get('/api/chat/'+account+'/settings')
  for(const key of ['enabled','model','processing_consent','save_history'])assert.equal(after[key],settings[key])
  facts.push({account,total:classification.total_local,classified:classification.classified,required_review:classification.review_required,untouched:classification.untouched})
 }
 await page.setViewportSize({width:390,height:844})
 for(const path of ['settings','orders','categories','review','subscriptions','spam','trash','cleanup-review']){
  await page.goto(base+'/'+path+'?account=personal')
  if(path==='settings')await page.getByRole('heading',{name:'Privacy',exact:true}).waitFor()
  else if(path==='orders')await page.getByRole('group',{name:'Smart sections',exact:true}).waitFor()
  else if(path==='categories'||path==='review')await page.getByRole('region',{name:'Mail organization',exact:true}).locator('.v5-mailbox-pagination').waitFor()
  else if(path==='subscriptions')await page.getByLabel('Subscription status',{exact:true}).waitFor()
  else await page.locator('.v5-mailbox-pagination').waitFor()
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,path)
 }
 assert.deepEqual(blocked,[]);assert.deepEqual(errors,[])
 console.log(JSON.stringify({result:'PASS published UX GET-only',accounts:facts,non_get:0,errors:0,external_destinations:'blocked',gmail_mutations:0,gemini_requests:0,historical_sync:'not started',user_preferences:'preserved'}))
}finally{await browser.close()}
