// Real deployed settings; GET only. Never submit keys, mail permissions or tests.
import assert from 'node:assert/strict'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const browser=await chromium.launch({headless:true,chromiumSandbox:true})
try {
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'}),errors=[],writes=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/**',route=>route.request().method()==='GET'?route.continue():(writes.push(route.request().url()),route.abort()))
 for(const account of ['personal','university']){
  await page.goto(`http://127.0.0.1:8000/settings?account=${account}`)
  const card=name=>page.getByRole('article').filter({has:page.getByRole('heading',{name,exact:true})})
  await card('Groq').getByRole('button',{name:'Change key'}).waitFor({timeout:60000})
  for(const name of ['Gemini','Groq','Mistral','Ollama','Z.AI','SiliconFlow'])await card(name).waitFor()
  for(const name of ['NVIDIA NIM','OpenRouter','SambaNova','Cerebras','OpenAI','Cloudflare Workers AI'])assert.equal(await card(name).count(),0)
  const sf=card('SiliconFlow');await sf.getByRole('button',{name:'Add key',exact:true}).waitFor();assert.equal(await sf.getByRole('button',{name:'Allow this account'}).count(),0)
  assert.equal(await page.getByRole('button',{name:'Qualify selected model'}).count(),0)
  assert.equal(await page.getByText('Qualify connected AI providers',{exact:true}).isVisible().catch(()=>false),false)
  await sf.getByRole('button',{name:'Add key',exact:true}).click()
  const dialog=page.getByRole('dialog',{name:'SiliconFlow setup'})
  assert.equal(await dialog.getByLabel('New provider API key').inputValue(),'')
  assert.equal(await dialog.getByLabel('New provider API key').getAttribute('type'),'password')
  await dialog.getByRole('button',{name:'Save and connect'}).waitFor();assert.equal(await dialog.locator('select').count(),0);assert.equal(await dialog.locator('input').count(),1)
  assert.match(await dialog.innerText(),/No inference or email is sent/)
  for(const width of [1360,390]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`/tmp/one-step-ai-${account}-${width}.png`,fullPage:true})}
  await dialog.getByRole('button',{name:'Close',exact:true}).click()
 }
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
 console.log('PASS real Personal/University settings, five existing routes plus SiliconFlow setup, archived routes absent, diagnostics collapsed, masked one-step key form, no manual model selection, desktop/mobile, GET only.')
} finally {await browser.close()}
