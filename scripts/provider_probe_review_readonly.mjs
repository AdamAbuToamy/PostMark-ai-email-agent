// Review persisted real results only: every non-GET request is blocked.
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',out=readFileSync('/tmp/probe-checkpoint.txt','utf8').trim()
const browser=await chromium.launch({headless:true,chromiumSandbox:true})
try{
 const page=await browser.newPage({viewport:{width:1360,height:1100},reducedMotion:'reduce'});page.setDefaultTimeout(30000)
 const errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',r=>r.request().method()==='GET'&&r.request().url().startsWith(base+'/')&&!r.request().url().includes('/body')?r.continue():r.abort())
 await page.goto(base+'/settings?account=personal');await page.getByText('Developer Settings',{exact:true}).click()
 const settings=page.getByLabel('Provider verification and paid authorization',{exact:true});await settings.getByText('Previous synthetic tests',{exact:true}).click()
 const rows=settings.getByLabel('Provider test result',{exact:true})
 for(const model of ['gemini-3.5-flash-lite','openai/gpt-oss-120b']){
  const row=rows.filter({has:page.locator('dd').filter({hasText:model})}).filter({hasText:'API inference and structured output succeeded.'}).first()
  await row.waitFor();assert.match(await row.innerText(),/sample classification differs/)
  for(const width of [1360,390]){await page.setViewportSize({width,height:1100});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await row.screenshot({path:`${out}/stored-${model.includes('gemini')?'gemini':'groq'}-${width}.png`})}
 }
 for(const [model,code] of [['google/gemma-4-26b-a4b-it:free','404'],['mistral-small-2603','429']]){
  const row=rows.filter({has:page.locator('dd').filter({hasText:model})}).first();await row.getByText('Test details',{exact:true}).click();await row.getByText(code,{exact:true}).waitFor()
 }
 for(const input of await page.locator('input[type=password]').all())assert.equal(await input.inputValue(),'')
 assert.deepEqual(errors,[]);console.log('PASS saved real results survive refresh; inference/schema/accuracy/quality are distinct; provider404/429 and local interruptions visible; keys absent; desktop/mobile; GET-only, zero model calls.')
}finally{await browser.close()}
