// Published UI verification. GET only, no key writes, model tests or Gmail reads.
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',out=readFileSync('/tmp/ux-control-checkpoint.txt','utf8').trim()
const browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(20000)
 const errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',r=>r.request().method()==='GET'&&r.request().url().startsWith(base+'/')&&!r.request().url().includes('/body')?r.continue():r.abort())
 const get=p=>page.request.get(base+p).then(r=>r.json())
 for(const account of ['personal','university']){
  const actual=await get('/api/hybrid/'+account)
  await page.goto(`${base}/settings?account=${account}`)
  const engine=page.getByRole('region',{name:'AI Engine',exact:true});await engine.getByRole('article',{name:'Gemini provider'}).waitFor()
  await page.waitForFunction(()=>document.querySelector('[role="switch"][aria-label="Autonomous organization"]')?.disabled===false)
  assert.equal(await engine.getByLabel('AI engine preference').inputValue(),actual.preference)
  assert.equal(await engine.getByRole('switch',{name:'Autonomous organization'}).getAttribute('aria-checked'),String(actual.local_policy.enabled))
  assert.equal(await engine.getByLabel('Ollama endpoint').count(),0)
  const names={gemini:'Gemini',ollama:'Ollama',groq:'Groq',openrouter:'OpenRouter',cerebras:'Cerebras',mistral:'Mistral',openai:'OpenAI'}
  for(const p of actual.providers){const card=engine.getByRole('article',{name:names[p.provider]+' provider',exact:true});await card.getByText(p.connection,{exact:true}).waitFor();assert.equal(await card.getByLabel('Enable '+names[p.provider]).isChecked(),!!p.enabled)}
  for(const width of [1360,390]){
   await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   await page.screenshot({path:`${out}/settings-live-${account}-${width}.png`,fullPage:true})
  }
 }
 const sync=await Promise.all(['personal','university'].map(a=>get('/api/control/'+a)))
 for(const width of [1360,390]){
  await page.setViewportSize({width,height:1000});await page.goto(base+'/sync-history?account=personal')
  for(const item of sync){const card=page.getByRole('article',{name:item.account+' historical sync',exact:true});await card.locator('.v5-status-badge').filter({hasText:new RegExp('^'+item.sync.state+'$')}).waitFor();assert.deepEqual((await card.locator('.pm-history-counts dd').allTextContents()).map(Number),[item.sync.discovered,item.sync.bodies_available,item.sync.bodies_remaining])}
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:`${out}/sync-live-${width}.png`,fullPage:true})
 }
 assert.deepEqual(errors,[])
 console.log('PASS real SQLite-backed provider flags, account-local policy/mode, Developer collapsed, historical discovery/body counts/state, desktop/mobile. GET-only; no provider tests, AI or Gmail actions.')
}finally{await browser.close()}
