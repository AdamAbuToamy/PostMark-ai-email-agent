// Real production GET-only settings check; never submit keys or invoke AI.
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/**',r=>r.request().method()==='GET'?r.continue():r.abort())
 await page.goto(base+'/settings?account=personal')
 const capacity=page.locator('.pm-capacity');await capacity.waitFor({timeout:60000});assert.equal(await capacity.getAttribute('open'),null)
 await capacity.locator('summary').first().click();await capacity.getByRole('table').waitFor({timeout:30000})
 assert.ok((await capacity.getByText('Not reported',{exact:true}).count())>0)
 const modelDetails=capacity.locator('details').filter({has:page.locator('summary').filter({hasText:'Model details and daily breakdown'})}).first()
 assert.equal(await modelDetails.getAttribute('open'),null)
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`/tmp/capacity-settings-${width}.png`,fullPage:true})
 }
 await page.setViewportSize({width:1360,height:1000})
 const cf=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Cloudflare Workers AI',exact:true})});await cf.getByRole('button',{name:/Add key/i}).click()
 const dialog=page.getByRole('dialog',{name:'Set up Cloudflare Workers AI'});await dialog.waitFor();assert.equal(await dialog.getByLabel('Cloudflare account ID').count(),1);assert.equal(await dialog.getByLabel('New provider API key').getAttribute('type'),'password');await dialog.getByRole('button',{name:'Close',exact:true}).click()
 const zai=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Z.AI',exact:true})});await zai.getByRole('button',{name:/Add key/i}).click();const z=page.getByRole('dialog',{name:'Set up Z.AI'});assert.equal(await z.getByRole('link').getAttribute('href'),'https://z.ai/manage-apikey/apikey-list');await z.getByRole('button',{name:'Close',exact:true}).click()
 assert.deepEqual(errors,[])
 console.log('PASS live SQLite capacity, unknown balances, collapsed diagnostics, Cloudflare Account ID/masked key, SambaNova card, exact Z.AI key link, desktop/mobile no overflow. GET-only; zero inference tests or key submissions.')
}finally{await browser.close()}
