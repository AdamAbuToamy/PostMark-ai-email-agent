// Isolated browser, all page HTTP brokered by the Python parent. No model or credentials.
import {chromium} from 'playwright'
import readline from 'node:readline'
import {createHash} from 'node:crypto'
import {inspect,evidence,isEmail,preference,reasonField,allScope,destructive,cancelAction,pendingTopic,scopedCancel} from './forms.mjs'
const lines=readline.createInterface({input:process.stdin}),pending=new Map(),commands=[]
let wake
lines.on('line',line=>{const value=JSON.parse(line);if(value.type==='start'||value.type==='continue'){if(wake){wake(value);wake=null}else commands.push(value)}else{pending.get(value.seq)?.(value);pending.delete(value.seq)}})
const command=()=>commands.length?Promise.resolve(commands.shift()):new Promise(resolve=>wake=resolve)
let input=await command(),browser,context,page,seq=0,network=0,redirects=0,steps=0,stopped=null,phase=false,paused=false,nextNavigation=null,readOnly=false,submissionDispatched=false,submissionAttempted=false,phaseKind=null
const seen=new Set(),trace=[],origin=new URL(input.url).origin
const send=value=>process.stdout.write(JSON.stringify(value)+'\n')
const stop=(reason,extra={})=>stopped??={state:'needs_action',reason,...extra}
const signature=state=>createHash('sha256').update(JSON.stringify(state)).digest('hex')
let lastSnapshot=null
async function request(req){
 const url=new URL(req.url())
 if(paused)return {error:'Paused session'}
 if(url.protocol!=='https:'||url.origin!==origin){if(req.isNavigationRequest()&&req.frame()===page.mainFrame())stop(url.protocol==='https:'?'destination_changed':'unsafe_navigation',url.protocol==='https:'?{proposed_destination:url.href}:{});return {error:'Unapproved origin'}}
 if(!['GET','HEAD'].includes(req.method())&&!(phase&&!readOnly&&req.method()==='POST')){stop('unapproved_request');return {error:'Unapproved request'}}
 if(++network>60){stop('network_budget');return {error:'Network bound'}}
 const id=++seq,response=new Promise(resolve=>pending.set(id,resolve))
 send({type:'request',seq:id,url:url.href,method:req.method(),headers:await req.allHeaders(),body:req.postDataBuffer()?.toString('base64')??'',approved_action:phase&&!readOnly,action_kind:phaseKind})
 const value=await response
 if(value.cancellation_submitted)submissionDispatched=true
 if(value.cancellation_attempted)submissionAttempted=true
 return value
}
async function navigate(url){nextNavigation=null;try{await page.goto(url,{waitUntil:'domcontentloaded',timeout:15000})}catch(error){if(!stopped&&!nextNavigation)stop('navigation_failed',{failure:{kind:error.name,reason:'Approved navigation failed before a reliable page result'}})}}
async function round(resuming){
 stopped=null
 if(resuming&&!readOnly&&lastSnapshot&&input.expected_snapshot!==lastSnapshot.snapshot_id){stop('page_changed');return stopped}
 for(let count=0;count<8&&!stopped;count++){
  while(nextNavigation&&!stopped)await navigate(nextNavigation)
  if(stopped)break
  await page.waitForLoadState('networkidle',{timeout:1500}).catch(()=>{});await page.waitForTimeout(150)
  const state=await inspect(page),text=state.excerpt
  const currentSignature=signature(state)
  if(resuming&&!readOnly&&count===0&&lastSnapshot&&currentSignature!==lastSnapshot.snapshot_id){stop('page_changed');break}
  if(await page.locator('input[type=password],input[autocomplete=one-time-code],iframe[src*="captcha"],[data-sitekey],.g-recaptcha').count()||/captcha|verify (?:you are|that you are) human|تأكيد أنك إنسان/i.test(text)||/log ?in|sign ?in|multi.factor|verification code/i.test(state.title)){stop('login_captcha_or_mfa');break}
  if(destructive.test(state.title)){stop('scope_change_or_unsupported');break}
  if(state.errors.length){stop('visible_form_error');break}
  const confirmed=evidence(text,/(?:you(?: have been| are(?: now)?|['’]ve been|['’]re(?: now)?) (?:successfully )?unsubscribed|^successfully unsubscribed$|^unsubscribe (?:successful|confirmed)$|unsubscription (?:confirmed|successful)|you have been removed from (?:our|the) (?:mailing|email) list|your (?:email )?preferences (?:have been |were )?(?:saved|updated)|تم إلغاء اشتراكك|تم الغاء اشتراكك)/i)
  // A preferences-save statement alone is not proof of unsubscribe.
  if(confirmed&&!/preferences/i.test(confirmed))return {state:'cancelled',reason:'website_confirmation',evidence:confirmed}
  if(confirmed&&trace.some(t=>t.preference_changes))return {state:'requested',reason:'preferences_updated',evidence:confirmed}
  const receipt=evidence(text,/(?:unsubscribe request (?:received|submitted)|check your (?:email|inbox) to confirm|confirmation email (?:has been )?sent|تم إرسال طلب إلغاء)/i)
  if(receipt)return {state:'requested',reason:'website_request_received',evidence:receipt}
  if(readOnly){stop('confirmation_uncertain');break}
  if(steps>=12){stop('step_budget');break}
  const fields=state.fields.filter(f=>!f.disabled&&!['submit','button'].includes(f.type)),email=fields.filter(isEmail),choices=fields.filter(preference)
  const unknown=fields.filter(f=>!isEmail(f)&&!preference(f)&&!((f.type==='textarea'||reasonField(f))&&!f.required))
  if(unknown.length){stop('unsupported_interactive_requirement');break}
  const topic=pendingTopic(text)
  const allControl=state.buttons.find(b=>!b.disabled&&allScope.test(b.label)&&/unsubscribe|opt.out|stop receiving/i.test(b.label))
  const allChoice=choices.find(f=>allScope.test(f.label))
  const onlyAll=allChoice&&!choices.some(f=>f.type==='checkbox')&&!choices.some(f=>!allScope.test(f.label)&&/unsubscribe|opt.out/i.test(f.label))
  if((allScope.test(state.title)||allControl&&!state.buttons.some(b=>b!==allControl&&cancelAction.test(b.label))||allChoice?.checked||onlyAll)&&!input.allow_all){stop('all_scope_requires_approval');break}
  if(email.length&&!input.recipient_email){stop('recipient_consent_required');break}
  const selected=new Set(input.topic_ids??[])
  const normalize=value=>value.toLowerCase().replace(/[^\p{L}\p{N}]/gu,'')
  if(topic&&!input.allow_all){
   const current=choices.filter(f=>!allScope.test(f.label)&&normalize(f.label.replace(/unsubscribe(?: from)?/i,''))===normalize(topic))
   if(current.length===1)selected.add(current[0].id)
   if(choices.length===1&&!allScope.test(choices[0].label)&&/^(?:yes[, ]*)?(?:confirm )?unsubscribe(?: me)?[.!]?$/i.test(choices[0].label))selected.add(choices[0].id)
  }
  if(choices.length&&!input.allow_all&&!selected.size&&!choices.every(f=>f.type==='radio'&&/unsubscribe|subscribe/i.test(f.label))){stop('preference_choice_required');break}
  for(const field of email)await page.locator(`[data-postmark-field="${field.id}"]`).fill(input.recipient_email)
  let changed=0
  for(const field of choices){
   const control=page.locator(`[data-postmark-field="${field.id}"]`)
   if(allScope.test(field.label)){if(input.allow_all){await control.setChecked(true);changed++}continue}
   if(field.type==='radio'){
    if(/unsubscribe|opt.out|stop receiving|إلغاء/i.test(field.label)&&(!allScope.test(field.group)||input.allow_all)){await control.check();changed++}
   }else if(selected.has(field.id)||input.allow_all){await control.setChecked(/unsubscribe|opt.out|إلغاء/i.test(field.label));changed++}
  }
  const target=state.buttons.slice().sort((a,b)=>Number(/^(continue|next|manage)/i.test(a.label))-Number(/^(continue|next|manage)/i.test(b.label))).find(b=>!b.disabled&&!destructive.test(b.label)&&scopedCancel(b.label,topic)&&(!/manage.*all|all.*preferences/i.test(b.label))&&(!allScope.test(b.label)||input.allow_all))
  if(!target){stop('unsupported_interaction');break}
  if(!/email|newsletter|mailing list|marketing|نشرة|رسائل|اشتراك/i.test(text)){stop('mailing_list_scope_not_established');break}
  const control=page.locator(`[data-postmark-control="${target.id}"]`)
  const destination=await control.evaluate(e=>e.closest('form')?.action??e.href??location.href)
  const fingerprint=target.label+'|'+destination+'|'+(topic??'')
  if(seen.has(fingerprint)){stop('interaction_did_not_advance');break}
  input.topic_ids=[]
  seen.add(fingerprint);steps++
  trace.push({step:steps,control:target.label,origin,preference_changes:changed,recipient_supplied:email.length>0})
  send({type:'event',event:'website_control_approved',detail:trace.at(-1)})
  phaseKind=/^(continue|next|manage.*preferences)$/i.test(target.label)?'navigate':'cancel'
  phase=true
  try{await control.click({timeout:5000});await page.waitForLoadState('domcontentloaded',{timeout:5000}).catch(()=>{});await page.waitForTimeout(300)}catch(error){if(!stopped&&!nextNavigation)stop('interaction_uncertain',{failure:{kind:error.name,reason:'The control interaction did not produce a reliable completion; no repeat'}})}finally{phase=false;phaseKind=null}
 }
 return stopped??{state:'needs_action',reason:'step_budget'}
}
try{
 browser=await chromium.launch({headless:true,chromiumSandbox:true,args:['--disable-background-networking','--disable-component-update','--disable-sync','--disable-extensions','--disable-quic','--dns-prefetch-disable','--force-webrtc-ip-handling-policy=disable_non_proxied_udp']})
 context=await browser.newContext({acceptDownloads:false,serviceWorkers:'block',permissions:[],ignoreHTTPSErrors:false})
 await context.addInitScript(()=>{for(const key of ['RTCPeerConnection','webkitRTCPeerConnection','WebTransport','WebSocket','EventSource'])try{Object.defineProperty(window,key,{value:undefined})}catch{}})
 await context.routeWebSocket('**/*',socket=>socket.close())
 page=await context.newPage();page.setDefaultTimeout(5000)
 context.on('page',extra=>{if(extra!==page){stop('unexpected_popup');void extra.close()}})
 page.on('download',download=>{stop('download_requested');void download.cancel()});page.on('dialog',dialog=>{stop('unexpected_dialog');void dialog.dismiss()})
 await context.route('**/*',async route=>{
  try{
   const req=route.request();if(req.frame()!==page.mainFrame()&&req.resourceType()==='document'){stop('embedded_frame');return route.abort()}
   const value=await request(req)
   if(value.error){if(!paused&&!stopped)stop(value.error_kind==='unsafe_destination'?'unsafe_navigation':'network_unavailable');return route.abort()}
   if(value.status>=300&&value.status<400){
    if(!req.isNavigationRequest()||req.frame()!==page.mainFrame())return route.abort()
    if(!value.headers.location){stop('invalid_redirect');return route.abort()}
    const target=new URL(value.headers.location,req.url())
    if(target.origin!==origin){stop(target.protocol==='https:'?'destination_changed':'unsafe_navigation',target.protocol==='https:'?{proposed_destination:target.href}:{});return route.abort()}
    if(++redirects>4){stop('redirect_budget');return route.abort()}
    if([307,308].includes(value.status)&&!['GET','HEAD'].includes(req.method())){stop('redirect_replay_not_allowed');return route.abort()}
    nextNavigation=target.href;return route.fulfill({status:200,headers:{'content-type':'text/html',...(value.headers['set-cookie']?{'set-cookie':value.headers['set-cookie']}: {})},body:'<main>Continuing approved same-origin redirect</main>'})
   }
   if(value.status>=400&&req.isNavigationRequest())stop('website_http_error',{http_status:value.status})
   await route.fulfill({status:value.status,headers:value.headers,body:Buffer.from(value.body,'base64')})
  }catch{if(!paused)stop('network_unavailable');await route.abort().catch(()=>{})}
 })
 await navigate(input.url)
 let resuming=false
 while(true){
  let result
  try{result=stopped??await round(resuming)}catch(error){result={state:'needs_action',reason:'browser_execution_error',failure:{kind:error.name,reason:String(error.message).replace(/https?:\/\/[^\s<>]+/g,'[link]').slice(0,1000)}}}
  try{const state=await inspect(page);lastSnapshot={...state,snapshot_id:signature(state)};result.page_state=lastSnapshot}catch{}
  result={...result,trace,network_requests:network,submitted:submissionDispatched,submission_attempted:submissionAttempted}
  try{result.page_url=page.url()}catch{}
  if(submissionAttempted&&result.state==='needs_action'&&['unsupported_interaction','visible_form_error','step_budget','unsupported_interactive_requirement'].includes(result.reason))result.reason='confirmation_uncertain'
  const uncertain=['browser_execution_error','interaction_uncertain','confirmation_uncertain','interaction_did_not_advance','network_unavailable','result_uncertain'].includes(result.reason)
  const retain=input.keep_session&&result.state==='needs_action'&&result.page_state?.origin===origin
  send({type:'result',...result,resumable:!!retain,inspect_only:uncertain||readOnly})
  if(!retain)break
  paused=true;const next=await command();paused=false
  readOnly=uncertain||readOnly||next.inspect_only===true
  input={...input,...next};resuming=true;stopped=null
 }
}catch(error){send({type:'result',state:'needs_action',reason:'browser_execution_error',failure:{kind:error.name,reason:'Browser execution failed; page state may be incomplete. No automatic replay.'},trace})}
finally{await context?.close().catch(()=>{});await browser?.close().catch(()=>{});lines.close();process.stdin.pause()}
