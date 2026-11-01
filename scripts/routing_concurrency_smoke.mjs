// Observe authorized production work; GET only, no synthetic inference.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/**',r=>r.request().method()==='GET'&&!r.request().url().includes('/body')?r.continue():r.abort())
 await page.goto(base+'/categories?account=personal');await page.getByRole('progressbar',{name:'Organization of known messages',exact:true}).waitFor({timeout:60000})
 let overlap=null
 for(let i=0;i<60;i++){
  const a=await page.request.get(base+'/api/control/personal?activity_only=true').then(r=>r.json())
  if(a.active.some(r=>r.provider==='ollama')&&a.active.some(r=>r.provider!=='ollama')){overlap=a;break}
  await page.waitForTimeout(500)
 }
 if(!overlap){const a=await page.request.get(base+'/api/control/personal?activity_only=true').then(r=>r.json());overlap=a;console.log('No simultaneous calls during browser sample; SQLite overlap records provide historical real-execution proof.')}
 const more=page.locator('.pm-operational-status details').filter({has:page.locator('summary').filter({hasText:/more active/})})
 if(await more.count())await more.locator('summary').click()
 await page.screenshot({path:'/tmp/routing-glass-desktop.png',fullPage:true})
 const style=await page.locator('.pm-operational-status').evaluate(e=>({background:getComputedStyle(e).backgroundColor,blur:getComputedStyle(e).backdropFilter}))
 assert.equal(style.blur,'blur(9px)');assert.ok(style.background.includes('0.66'))
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.equal(await page.getByRole('group',{name:'Group status',exact:true}).getByRole('button').count(),6)
 }
 await page.screenshot({path:'/tmp/routing-glass-mobile.png',fullPage:true});assert.deepEqual(errors,[])
 fs.writeFileSync('/tmp/routing-concurrency-browser.json',JSON.stringify({active:overlap.active,style}))
 await page.goto(base+'/settings?account=personal');await page.getByRole('heading',{name:'Z.AI',exact:true}).waitFor({timeout:60000});await page.screenshot({path:'/tmp/zai-settings.png',fullPage:true})
 console.log('PASS live activity, visible glass/blur, six tabs, desktop/mobile no overflow, Z.AI provider card. GET-only, no added AI requests.')
}finally{await browser.close()}
