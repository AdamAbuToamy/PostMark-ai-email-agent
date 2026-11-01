import { useState } from 'react'
import type { AccountId } from '../api/emails'
import { postLocal } from '../api/actions'
import { readLocal } from '../api/mailboxes'
import type { ManualMethod } from '../api/subscriptions'
import { useRoute } from '../routing'
export function SubscriptionManualActions({account,id,messageId,destinations,onChanged,expanded=false}:{expanded?:boolean;account:AccountId;id:string;messageId:string;destinations:string[];onChanged:()=>void}) {
  const {route,navigate}=useRoute()
  const [method,setMethod]=useState<ManualMethod|null>(null)
  const [notice,setNotice]=useState('')
  const [busy,setBusy]=useState(false)
  const [audit,setAudit]=useState<{event:string;note:string;created_at:string;result?:{items?:{status:string;message?:string}[]}}[]>([])
  async function review(index:number){
    if(!window.confirm(`Review this exact manual method for ${account}? ${destinations[index]} Postmark will check HTTPS public DNS only, without visiting or submitting the link. Email methods are copy-only.`))return
    setBusy(true);setNotice('');setMethod(null)
    try{setMethod(await postLocal<ManualMethod>(`/api/subscriptions/${account}/${id}/manual-method`,{method_index:index,approved:true}));onChanged()}
    catch(err){setNotice(err instanceof Error?err.message:'Manual method unavailable')}
    finally{setBusy(false)}
  }
  async function handled(){
    if(!window.confirm('Mark this list handled locally? This records your review only; it does not unsubscribe, block a sender or change Gmail.'))return
    try{await postLocal(`/api/subscriptions/${account}/${id}/handled`,{approved:true,note:'Reviewed manually; marked handled by the user, cancellation not independently verified.'});setNotice('Handled locally. Cancellation and future delivery are not verified.');onChanged()}
    catch(err){setNotice(err instanceof Error?err.message:'Unable to save local state')}
  }
  return <details open={expanded||undefined} className="v5-technical-details"><summary>Manual actions and next steps</summary><p>Review the sender and original first. If the classification is wrong or pending, use Change Category → Promotions after reviewing the message, then check automatic eligibility again. A category decision does not authenticate its sender.</p><button className="v5-small-button" onClick={()=>navigate({page:route.page,account,messageId})}>Review original and category</button>{destinations.map((value,index)=><div key={value}><code className="v5-manual-destination">{value}</code><button className="v5-small-button" disabled={busy} onClick={()=>void review(index)}>Review manual method {index+1}</button></div>)}{!destinations.length&&<p>No supported header method was observed. Inspect the original or the sender’s trusted account settings. Postmark cannot complete cancellation without a legitimate method.</p>}{method&&<div className="v5-third-party-review"><p>{method.instructions}</p>{method.kind==='https'?<a className="v5-small-button" href={method.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Open reviewed manual unsubscribe page</a>:<><p>Recipient: {method.address}</p><p>Subject: {method.subject}</p><pre>{method.body}</pre><button className="v5-small-button" onClick={()=>void navigator.clipboard.writeText(`To: ${method.address}\nSubject: ${method.subject}\n\n${method.body}`).then(()=>setNotice('Email fields copied. Review and send yourself if appropriate.')).catch(()=>setNotice('Copy unavailable; select the displayed fields manually.'))}>Copy manual email fields</button></>}</div>}<div className="v5-preview-buttons"><button className="v5-small-button" disabled={busy} onClick={()=>void handled()}>Mark handled locally</button><button className="v5-small-button" disabled={busy} onClick={()=>navigate({page:route.page,account,messageId})}>Open message for separate Trash action</button><button className="v5-small-button" disabled title="Sender blocking is unavailable; no Gmail filter is created">Block sender unavailable</button></div><button className="v5-small-button" onClick={()=>void readLocal<typeof audit>(`/api/subscriptions/${account}/${id}/history`).then(setAudit).catch(()=>setNotice('History unavailable'))}>Show manual action history</button>{audit.map((event,index)=><p key={index}>{event.created_at} · {event.event} · {event.note}{event.result?.items?.map((item,i)=><span key={i}> · {item.status}: {item.message}</span>)}</p>)}{notice&&<p role="status">{notice}</p>}</details>
}
