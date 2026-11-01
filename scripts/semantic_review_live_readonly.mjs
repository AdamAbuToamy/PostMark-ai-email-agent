// Published local UI only; block non-GET/external/body routes. Screenshots private.
import {readFileSync} from 'node:fs'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
import assert from 'node:assert/strict'
const output=readFileSync('/tmp/semantic-review-release.txt','utf8').trim(),base='http://127.0.0.1:8000'
const browser=await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.CHROMIUM_PATH})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[],blocked=[]
 page.on('pageerror',()=>errors.push('page error'))
 await page.route('**/*',r=>{const q=r.request();if(q.method()!=='GET'||q.url().includes('/body')){blocked.push(q.method());return r.abort()}return q.url().startsWith(base+'/')?r.continue():r.abort()})
 const get=async path=>{const r=await page.request.get(base+path);assert.equal(r.status(),200);return r.json()}
 const settings=await get('/api/chat/university/settings'),before=await get('/api/chat/personal/settings')
 assert.equal(settings.enabled,false);assert.equal(settings.processing_consent,false)
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000})
  for(const view of ['spam','trash']){
   await page.goto(base+'/'+view+'?account=personal');await page.locator('.v5-mailbox-pagination').waitFor()
   const layout=await page.evaluate(()=>{const n=document.querySelector('.v5-mail-management'),r=n.getBoundingClientRect(),p=+getComputedStyle(n).paddingLeft.replace('px','');return {overflow:document.documentElement.scrollWidth>innerWidth,pad:p,inside:[...n.querySelectorAll('.v5-management-toolbar input,.v5-management-toolbar button,.v5-managed-row')].every(x=>{const b=x.getBoundingClientRect();return b.left>=r.left+p-1&&b.right<=r.right-p+1})}})
   assert.equal(layout.overflow,false);assert.equal(layout.inside,true);assert.ok(layout.pad>=16)
   await page.addStyleTag({content:'.v5-managed-message .v5-message-data,.v5-managed-meta,.v5-managed-row pre{filter:blur(7px)}'})
   await page.screenshot({path:output+`/published-${view}-${width}.png`,fullPage:true})
  }
  await page.goto(base+'/bills?account=personal');await page.getByRole('group',{name:'Smart sections'}).waitFor()
  await page.getByRole('group',{name:'Smart sections'}).getByRole('button',{name:/^Receipt\b/}).click()
  await page.waitForFunction(()=>document.querySelector('.pm-section-chips button[aria-pressed=true]')?.textContent.startsWith('Receipt'))
  const receipt=await get('/api/category-mailbox/personal/bills?state=all'),section=receipt.sections.find(s=>s.name==='Receipt')
  const filtered=await get('/api/category-mailbox/personal/bills?state=all&section='+section.id)
  assert.equal(filtered.total,5);assert.ok(filtered.items.some(i=>i.semantic?.source==='gemini'&&!i.semantic.input_coverage.body_supplied));assert.ok(filtered.items.every(i=>i.section_id!=='other'))
 }
 const after=await get('/api/chat/university/settings'),personal=await get('/api/chat/personal/settings')
 for(const key of ['enabled','processing_consent','model','save_history'])assert.equal(after[key],settings[key])
 assert.equal(personal.usage.used,before.usage.used);assert.equal((await get('/api/organizer/university')).recurring.enabled,false)
 assert.deepEqual(errors,[]);assert.deepEqual(blocked,[])
 console.log('PASS published desktop/mobile Trash+Spam bounds and private masked screenshots; accepted AI receipt visible in Receipt5; actual body-coverage metadata visible via API; University/personal AI quota unchanged; recurring disabled. Zero non-GET/body/AI/Gmail operations.')
}finally{await browser.close()}
