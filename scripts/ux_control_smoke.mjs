// Isolated SQLite-backed UI controls; no real AI/Gmail or provider discovery.
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {setTimeout as pause} from 'node:timers/promises'
import {chromium} from '../browser/node_modules/playwright/index.mjs'
const server=spawn('.venv/bin/python',['scripts/batch3_smoke_server.py'],{env:{...process.env,PYTHONPATH:'src',UX_PRODUCT_FIXTURE:'1',FOCUSED_FIXTURE:'1'},stdio:['ignore','inherit','inherit']})
let browser
try{
 const base='http://127.0.0.1:8012'
 for(let i=0;i<80;i++){try{if((await fetch(base+'/api/health')).ok)break}catch{}await pause(200)}
 browser=await chromium.launch({headless:true,chromiumSandbox:true});const page=await browser.newPage({viewport:{width:1360,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(15000)
 const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.route('https://fonts.googleapis.com/**',r=>r.abort())
 const get=p=>page.request.get(base+p).then(r=>r.json())
 await page.goto(base+'/settings?account=personal');const engine=page.getByRole('region',{name:'AI Engine',exact:true})
 await engine.getByRole('heading',{name:'AI Engine',exact:true}).waitFor()
 for(const name of ['Gemini','Ollama','Groq','OpenRouter','Cerebras','Mistral','OpenAI'])await engine.getByRole('article',{name:name+' provider',exact:true}).waitFor()
 assert.equal(await engine.getByLabel('Private API key',{exact:true}).count(),0)
 assert.equal(await engine.getByLabel('Ollama endpoint',{exact:true}).count(),0)
 await engine.getByLabel('AI engine preference',{exact:true}).selectOption('local');await engine.getByText('Saved.',{exact:true}).waitFor()
 assert.equal((await get('/api/hybrid/personal')).preference,'local');assert.equal((await get('/api/hybrid/university')).preference,'auto')
 const groq=engine.getByRole('article',{name:'Groq provider',exact:true})
 await groq.getByRole('button',{name:'Add key',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'Set up Groq',exact:true});await dialog.getByLabel('New provider API key').fill('fixture-only-private-credential')
 assert.equal(await dialog.getByLabel('New provider API key').getAttribute('type'),'password')
 await dialog.getByRole('button',{name:'Save key securely',exact:true}).click();await dialog.getByRole('button',{name:'Test and discover models'}).waitFor()
 await dialog.getByRole('button',{name:'Close',exact:true}).click()
 assert.equal(await groq.getByLabel('Enable Groq').isChecked(),false)
 let state=await get('/api/hybrid/personal');assert.equal(state.providers.find(p=>p.provider==='groq').configured,true);assert.deepEqual(state.grants,[]);assert.equal(JSON.stringify(state).includes('fixture-only-private-credential'),false)
 await groq.getByLabel('Enable Groq').click();await engine.getByText('Saved.',{exact:true}).waitFor()
 await page.reload();await groq.getByRole('button',{name:'Change key'}).waitFor();assert.equal(await groq.getByLabel('Enable Groq').isChecked(),true)
 await groq.getByLabel('Enable Groq').click();await engine.getByText('Saved.',{exact:true}).waitFor();assert.equal(await groq.getByLabel('Enable Groq').isChecked(),false)
 assert.equal((await get('/api/hybrid/university')).providers.find(p=>p.provider==='groq').account_authorized,false)
 await page.goto(base+'/sync-history?account=personal')
 const card=page.getByRole('article',{name:'personal historical sync',exact:true});await card.getByRole('button',{name:'Resume Sync',exact:true}).click()
 await card.getByRole('button',{name:'Sync scheduled',exact:true}).waitFor();assert.equal((await get('/api/control/personal')).sync.enabled,true)
 assert.equal((await get('/api/control/university')).sync.enabled,false)
 await card.getByRole('button',{name:'Pause Sync',exact:true}).click();await card.getByRole('button',{name:'Resume Sync',exact:true}).waitFor();assert.equal((await get('/api/control/personal')).sync.state,'Paused')
 await page.reload();await card.getByRole('button',{name:'Resume Sync',exact:true}).waitFor()
 for(const width of [1360,390])for(const path of ['settings','sync-history','categories/other']){
  await page.setViewportSize({width,height:1000});await page.goto(`${base}/${path}?account=personal`)
  if(path==='settings')await engine.getByRole('article',{name:'Gemini provider'}).waitFor()
  else if(path==='sync-history')await card.getByRole('button',{name:'Resume Sync',exact:true}).waitFor()
  else{await page.getByLabel('Other AI organization',{exact:true}).waitFor();const p=await get('/api/hybrid/personal/other');assert.equal(Object.values(p.counts).reduce((a,b)=>a+b,0),p.total)}
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,path+' overflow')
  await page.screenshot({path:`/tmp/ux-control-${path.replace('/','-')}-${width}.png`,fullPage:true})
 }
 const final=await get('/fixture/state');assert.equal(final.model_calls,0);assert.equal(final.events.length,0);assert.deepEqual(errors,[])
 console.log('PASS SQLite-backed provider cards/guided secure key/controlled enabled state/account isolation/persisted mode, durable sync pause/resume with no spawned worker, Other exact states, desktop/mobile. External providers mocked; zero AI/Gmail.')
}finally{await browser?.close();server.kill('SIGTERM')}
