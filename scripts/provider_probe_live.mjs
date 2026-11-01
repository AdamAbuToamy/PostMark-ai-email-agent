// Explicit user grant: four synthetic requests total, one/provider, no retry.
// No metadata calls, mail submission, key writes, consent grants or model qualification.
import assert from 'node:assert/strict'
import {readFileSync,writeFileSync,existsSync} from 'node:fs'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const base='http://127.0.0.1:8000',out=readFileSync('/tmp/probe-checkpoint.txt','utf8').trim()
const tests=[['gemini','gemini-3.5-flash-lite'],['groq','openai/gpt-oss-120b'],['openrouter','google/gemma-4-26b-a4b-it:free'],['mistral','mistral-small-2603']]
const prior=existsSync(`${out}/live-probes.json`)?JSON.parse(readFileSync(`${out}/live-probes.json`,'utf8')):[]
const issued=existsSync(`${out}/live-probes-issued.json`)?JSON.parse(readFileSync(`${out}/live-probes-issued.json`,'utf8')):prior.map(r=>r.provider)
const browser=await chromium.launch({headless:true,chromiumSandbox:true});let sent=issued.length;const tried=new Set(issued),results=prior
try{
 const page=await browser.newPage({viewport:{width:1360,height:1100},reducedMotion:'reduce'});page.setDefaultTimeout(120000)
 await page.route('**/*',async r=>{
  const req=r.request(),url=req.url()
  if(req.method()==='GET'&&url.startsWith(base+'/')&&!url.includes('/body'))return r.continue()
  if(req.method()==='POST'){
   const test=tests.find(([provider])=>url===`${base}/api/hybrid/personal/providers/${provider}/probe`)
   const payload=req.postDataJSON()
   if(test&&sent<4&&!tried.has(test[0])&&payload.model===test[1]&&payload.approved&&payload.free_account_confirmed){sent++;tried.add(test[0]);writeFileSync(`${out}/live-probes-issued.json`,JSON.stringify([...tried]));return r.continue()}
  }
  return r.abort()
 })
 await page.goto(base+'/settings?account=personal')
 await page.getByRole('region',{name:'AI Engine',exact:true}).waitFor()
 await page.getByText('Developer Settings',{exact:true}).click()
 const section=page.getByLabel('Provider verification and paid authorization',{exact:true});await section.waitFor()
 await section.getByText('One-request live compatibility test',{exact:true}).click()
 for(const [provider,model] of tests){
  if(tried.has(provider))continue
  await section.getByLabel('Probe provider').selectOption(provider)
  await section.getByLabel('Probe model').selectOption(model)
  await section.getByLabel('I verified free eligibility for this account/model with billing disabled.',{exact:true}).check()
  await section.getByRole('button',{name:'Run one-request test',exact:true}).click()
  const responsePromise=page.waitForResponse(r=>r.url()===`${base}/api/hybrid/personal/providers/${provider}/probe`&&r.request().method()==='POST')
  await page.getByRole('dialog',{name:'Confirm action',exact:true}).getByRole('button',{name:'Confirm',exact:true}).click()
  const response=await responsePromise;const data=await response.json()
  results.push({provider,model,http:response.status(),result:data});writeFileSync(`${out}/live-probes.json`,JSON.stringify(results,null,2))
  const result=section.getByLabel('Provider test result',{exact:true});await result.waitFor()
  await result.getByText('Test details',{exact:true}).click()
  await result.getByText(provider,{exact:true}).waitFor()
  for(const width of [1360,390]){await page.setViewportSize({width,height:1100});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await result.screenshot({path:`${out}/probe-${provider}-${width}.png`})}
  await page.setViewportSize({width:1360,height:1100})
  console.log(provider,model,'HTTP',response.status(),JSON.stringify(data))
 }
 assert.equal(sent,4);console.log('Completed exactly four explicitly approved synthetic UI requests. No retries, metadata, mail content or account consent changes.')
}finally{await browser.close()}
