// Real production Settings, GET only; no discovery, testing, grants or inference.
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),writes=[],errors=[]
 page.on('pageerror',e=>errors.push(e.message));await page.route('**/api/**',route=>route.request().method()==='GET'?route.continue():(writes.push(route.request().url()),route.abort()))
 for(const account of ['personal','university']){
  await page.goto(`http://127.0.0.1:8000/settings?account=${account}`)
  const card=name=>page.getByRole('article').filter({has:page.getByRole('heading',{name,exact:true})})
  await card('Mistral').getByRole('button',{name:'Change key'}).waitFor({timeout:60000})
  for(const name of ['Gemini','Groq','Mistral','Z.AI','Ollama'])await card(name).waitFor()
  assert.equal(await page.locator('.pm-provider-card').count(),5)
  for(const name of ['SiliconFlow','NVIDIA NIM','Cloudflare Workers AI','OpenRouter','OpenAI','SambaNova','Cerebras'])assert.equal(await card(name).count(),0)
  if(account==='personal')assert.equal(await card('Z.AI').getByRole('button',{name:'Allow this account'}).count(),0)
  else assert.equal(await card('Z.AI').getByRole('button',{name:'Allow this account'}).count(),1)
  const developer=page.locator('details.pm-developer-settings');await developer.locator('summary').first().click();const providers=page.getByLabel('AI provider',{exact:true});await developer.getByText('Advanced providers',{exact:true}).click();await providers.waitFor()
  assert.deepEqual(await providers.locator('option').allTextContents(),['gemini','ollama','groq','mistral','zai'])
  await developer.locator('summary').first().click()
  for(const width of [1360,390]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`/tmp/final-free-ai-${account}-${width}.png`,fullPage:true})}
 }
 assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);console.log('PASS only five provider cards/model selectors; Personal ZAI existing consent preserved; University remains unauthorized; desktop/mobile; GET only.')
}finally{await browser.close()}
