// Page data is untrusted evidence. This module never evaluates page instructions.
export const allScope=/unsubscribe\s*(?:from\s*)?all|all (?:emails?|lists|topics|subscriptions|marketing)|opt.out.*all|جميع|الكل/i
export const destructive=/delete|close (?:my |the )?account|payment|billing|purchase|password|حذف الحساب/i
export const cancelAction=/^(?:(?:yes[,!]?\s*)?(?:confirm\s+)?unsubscribe(?:\s+(?:me|now|from (?:this|the) (?:newsletter|mailing list|emails?)))?|unsubscribe (?:from )?all(?: emails?)?|remove me(?: from (?:this|the) (?:list|newsletter))?|stop (?:these |marketing )?emails|confirm(?: cancellation| unsubscribe)?|continue|next|submit|save(?: (?:email )?preferences| changes)?|update(?: (?:email )?preferences)?|manage (?:email |newsletter )?preferences|الغاء الاشتراك|إلغاء الاشتراك|تأكيد إلغاء الاشتراك|تأكيد|متابعة|حفظ)$/i
export async function inspect(page){
 const state=await page.evaluate(()=>{
  const visible=element=>!!(element.getClientRects().length&&getComputedStyle(element).visibility!=='hidden')
  const clean=value=>String(value??'').replace(/https?:\/\/[^\s<>]+/g,'[link]').replace(/\s+/g,' ').slice(0,180)
  const fields=[...document.querySelectorAll('input:not([type=hidden]),textarea,select')].filter(visible).slice(0,30).map((element,index)=>{
   element.setAttribute('data-postmark-field',String(index))
   const label=clean([...element.labels??[]].map(node=>node.innerText).join(' ')||element.getAttribute('aria-label')||element.placeholder||element.name)
   return {id:String(index),type:element.type||element.tagName.toLowerCase(),name:clean(element.name),label,group:clean(element.closest('fieldset')?.querySelector('legend')?.innerText),checked:!!element.checked,required:element.required,disabled:element.disabled}
  })
  const buttons=[...document.querySelectorAll('button,a,input[type=submit],input[type=button]')].filter(visible).slice(0,30).map((element,index)=>{
   element.setAttribute('data-postmark-control',String(index))
   return {id:String(index),label:clean(element.innerText||element.value||element.getAttribute('aria-label')),disabled:element.disabled||element.getAttribute('aria-disabled')==='true'}
  })
  return {origin:location.origin,title:clean(document.title||document.querySelector('h1')?.innerText),text:clean(document.querySelector('main')?.innerText||document.body.innerText).slice(0,4000),
   excerpt:String(document.querySelector('main')?.innerText||document.body.innerText).replace(/https?:\/\/[^\s<>]+/g,'[link]').slice(0,4000),
   errors:[...document.querySelectorAll('[role=alert],.error,.errors,.validation-error,[aria-invalid=true]')].filter(visible).map(e=>clean(e.innerText||e.validationMessage)).filter(Boolean).slice(0,8),fields,buttons}
 })
 state.fields=state.fields.map(field=>({...field,purpose:isEmail(field)?'email':preference(field)?'topic':reasonField(field)?'reason':'unknown'}))
 return state
}
export function evidence(text,pattern){
 for(const line of text.split(/[\n.!?]+/)){
  if(/\b(?:not|never|failed|unsuccessful|if|when|once|after|example|click|press|select|choose|want|wish|can|to be)\b|لم |ليس |غير /i.test(line))continue
  const match=line.match(pattern);if(match)return match[0]
 }
 return null
}
export function isEmail(field){return field.type==='email'||field.type==='text'&&/email|e-mail|البريد/i.test(field.label+' '+field.name)}
export function reasonField(field){return /why|reason|feedback|سبب/i.test(field.label+' '+field.group+' '+field.name)}
export function preference(field){return !reasonField(field)&& ['checkbox','radio'].includes(field.type)&&!destructive.test(field.label)&&/email|newsletter|marketing|subscribe|updates|offers|announcements|رسائل|اشتراك|نشرة/i.test(field.label+' '+field.group)}

export function pendingTopic(text){
 const match=text.match(/do you (?:really )?want to unsubscribe from\s+([^?\n]+)\?/i)
 return match?.[1]?.trim()??null
}
export function scopedCancel(label,topic){
 if(cancelAction.test(label))return true
 if(!topic)return false
 const normalize=value=>value.toLowerCase().replace(/[’'".!?,]/g,'').replace(/\s+/g,' ').trim()
 const target=normalize(topic),actual=normalize(label)
 return [`unsubscribe from ${target}`,`unsubscribe me from ${target}`,`yes unsubscribe from ${target}`,`yes unsubscribe me from ${target}`,`confirm unsubscribe from ${target}`].includes(actual)
}
