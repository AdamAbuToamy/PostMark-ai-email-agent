// Published local effects and layout. No actions, body fetches or model requests.
import {readFileSync} from 'node:fs'
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const output=readFileSync('/tmp/local-agent-release.txt','utf8').trim(),base='http://127.0.0.1:8000'
const browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[],writes=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',route=>{const r=route.request();if(r.method()!=='GET'||r.url().includes('/body')){writes.push(r.method());return route.abort()}return r.url().startsWith(base+'/')?route.continue():route.abort()})
 const targets={}
 for(const account of ['personal','university']){
  const data=await page.request.get(base+'/api/organizer/'+account).then(r=>r.json())
  assert.equal(data.local_policy.enabled,true)
  const categories=await page.request.get(base+'/api/categories?account='+account).then(r=>r.json())
  if(account==='personal'){const applied=categories.find(c=>c.name==='View test results');assert.ok(applied);const result=await page.request.get(`${base}/api/category-mailbox/${account}/${applied.id}?state=all`).then(r=>r.json());assert.ok(result.total>=3)}
  const category=categories.find(c=>c.name==='Conversations');assert.ok(category);targets[account]=category.id
  const mailbox=await page.request.get(`${base}/api/category-mailbox/${account}/${category.id}?state=all`).then(r=>r.json());assert.ok(mailbox.total>0)
  await page.goto(`${base}/categories?account=${account}`)
  await page.getByLabel('Semantic organizer',{exact:true}).getByRole('button',{name:'On',exact:true}).waitFor()
  await page.getByRole('button',{name:'Open navigation',exact:true}).click()
  const nav=page.getByRole('navigation',{name:'Primary navigation'})
  await nav.getByRole('button',{name:'Other',exact:true}).waitFor()
  if(account==='personal')await nav.getByRole('button',{name:'View test results',exact:true}).waitFor()
  await nav.getByRole('button',{name:'Conversations',exact:true}).click()
  await page.locator('.v5-message').first().waitFor()
  await page.reload();await page.locator('.v5-message').first().waitFor()
 }
 assert.notEqual(targets.personal,targets.university)
 assert.equal((await page.request.get(`${base}/api/category-mailbox/university/${targets.personal}?state=all`)).status(),400)
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000})
  for(const path of ['categories','review','spam','trash','cleanup-review','subscriptions','settings']){
   await page.goto(`${base}/${path}?account=personal`)
   if(path==='categories')await page.getByLabel('Semantic organizer',{exact:true}).getByRole('button',{name:'On',exact:true}).waitFor()
   else if(path==='review'){
    const tabs=page.getByRole('group',{name:'Review views'});await tabs.waitFor()
    for(const label of ['Grouped decisions','Category Review','Spam','Trash','Decision history']){
     const button=tabs.getByRole('button',{name:label,exact:true});await button.click();assert.equal(await button.getAttribute('aria-pressed'),'true')
     const box=await button.boundingBox();assert.ok(box.x>=8&&box.x+box.width<=width-8)
    }
   }else if(path==='subscriptions'){
    const filter=page.getByLabel('Subscription status',{exact:true});await filter.waitFor();assert.equal(await filter.inputValue(),'active')
    await filter.selectOption('unsubscribed');await page.getByText(/list(s)?$/).first().waitFor()
   }else if(path==='settings')await page.getByRole('button',{name:/Old mail discovered|Sync all old mail/}).first().waitFor()
   else await page.getByPlaceholder('Search sender or subject…').waitFor()
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${path} width ${width}`)
   const section=page.locator('.v5-mail-section').first()
   if(['review','spam','trash','cleanup-review'].includes(path)){
    const paper=await section.boundingBox(),heading=await section.locator('h2').first().boundingBox()
    assert.ok(heading.x-paper.x>=15);assert.ok(heading.y-paper.y>=15)
   }
   await page.addStyleTag({content:'.v5-message,.v5-managed-row,.v5-review-row,.v5-organization-group ul,.pm-group-preview ul,.v5-subscription-row{filter:blur(7px)}'})
   await page.screenshot({path:`${output}/local-agent-${path}-${width}.png`,fullPage:true})
  }
 }
 const fixtureItems=[['active','Active fixture'],['unsubscribed','Cancelled fixture'],['manual_completed','Manual cancellation fixture'],['submitted','Pending request fixture']].map(([state,display_name],i)=>({id:'fixture-'+i,identity:'fixture-'+i,display_name,kind:'mailing_list',observed_count:1,message_ids:[],methods:[],manual_destinations:[],reason:'Test only',state:'observed',tracking:{state,label:state,later_message:false}}))
 await page.route(base+'/api/subscriptions/personal',r=>r.fulfill({json:{items:fixtureItems,coverage:'Synthetic subscription states',progress:{total:4,indexed:4,missing:0,ready:0,failed:0,skipped:0,exhausted:0,running:false,complete:true,page_size:20,coverage_limit:1000}}}))
 await page.goto(base+'/subscriptions?account=personal')
 await page.getByText('Active fixture',{exact:true}).waitFor()
 assert.equal(await page.getByText('Cancelled fixture',{exact:true}).count(),0)
 const status=page.getByLabel('Subscription status',{exact:true});await status.selectOption('unsubscribed')
 await page.getByText('Cancelled fixture',{exact:true}).waitFor();await page.getByText('Manual cancellation fixture',{exact:true}).waitFor()
 assert.equal(await page.getByText('Active fixture',{exact:true}).count(),0)
 await status.selectOption('pending');await page.getByText('Pending request fixture',{exact:true}).waitFor()
 assert.equal(await page.getByText('Cancelled fixture',{exact:true}).count(),0)
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
 console.log('PASS local organization read-only verification')
}finally{await browser.close()}
