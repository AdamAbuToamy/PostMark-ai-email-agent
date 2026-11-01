import { useRoute } from '../routing'
import { useEffect, useState } from 'react'
import type { AccountId, EmailMessage } from '../api/emails'
import { readLocal } from '../api/mailboxes'
import { postLocal, type ActionPreview } from '../api/actions'
import { AccountSelect } from './AccountSelect'
import { ActionApproval } from './ActionApproval'
import { useConfirmation } from './ConfirmAction'

type Mode='review'|'automatic'
interface Policy { revision:string; enabled:boolean; mode:Mode; exclusions:string[]; paused_reason:string|null }
interface CleanupItem { message_id:string; sender:string|null; subject:string|null; status:string; label_ids:string[]|null; updated_at:string; detail:{reason?:string} }
interface CleanupData { policy:Policy; gmail_modify:boolean; history:{cleaned_today:number;timezone:string;items:CleanupItem[];audit:unknown[]} }
const labels:Record<string,string>={queued:'Waiting',running:'In progress',review:'Ready for review',succeeded:'Moved to Trash',uncertain:'Needs verification',failed:'Failed',skipped:'Protected / skipped',cancelled:'Cancelled'}

export function AutomaticCleanup({account,onAccountChange,onOpen,onChanged,compact=false}:{compact?:boolean;account:AccountId;onAccountChange:(value:AccountId)=>void;onOpen:(message:EmailMessage)=>void;onChanged:()=>void}) {
 const {confirm,dialog}=useConfirmation()
 const {navigate}=useRoute()
 const [data,setData]=useState<CleanupData|null>(null)
 const [enabled,setEnabled]=useState(false),[mode,setMode]=useState<Mode>('review'),[exclusions,setExclusions]=useState('')
 const [busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[error,setError]=useState('')
 const [revision,setRevision]=useState(0),[preview,setPreview]=useState<ActionPreview|null>(null)
 function apply(value:CleanupData){setData(value);setEnabled(value.policy.enabled);setMode(value.policy.mode);setExclusions(value.policy.exclusions.join('\n'))}
 useEffect(()=>{const c=new AbortController();readLocal<CleanupData>(`/api/cleanup/${account}`,c.signal).then(value=>{if(!c.signal.aborted)apply(value)}).catch(()=>{if(!c.signal.aborted)setError('Cleanup settings unavailable')});return()=>c.abort()},[account,revision])
 async function save(nextEnabled=enabled,nextMode=mode,preserveSavedExclusions=false){
  if(!data||busy)return
  setBusy(true);setError('');setNotice('')
  try {
   const senders=preserveSavedExclusions?data.policy.exclusions:exclusions.split(/[\n,]+/).map(value=>value.trim().toLowerCase()).filter(Boolean)
   const changed=nextEnabled!==data.policy.enabled||nextMode!==data.policy.mode||JSON.stringify([...new Set(senders)].sort())!==JSON.stringify(data.policy.exclusions)
   let approved=false
   if(nextEnabled&&nextMode==='automatic'&&changed){
    approved=await confirm(`Enable automatic Trash for future advertisements in ${account==='personal'?'Personal Gmail':'University Gmail'}? Postmark will move only eligible new ads to Gmail Trash, up to 10 per sync, without asking per message. Orders, bills, security, academic and personal mail, manual classifications and protected senders are excluded. Historical mail is excluded. You can turn this off or restore messages anytime.`)
    if(!approved)return
   }
   apply(await postLocal<CleanupData>(`/api/cleanup/${account}`,{enabled:nextEnabled,mode:nextMode,exclusions:senders,approved,expected_revision:data.policy.revision}))
   setNotice(nextEnabled?(nextMode==='automatic'?'Automatic cleanup enabled for future eligible ads.':'Review only enabled. Gmail will not be changed.'):'Automatic cleanup is off.')
  }catch(e){setError(e instanceof Error?e.message:'Unable to save cleanup settings');try{apply(await readLocal<CleanupData>(`/api/cleanup/${account}`))}catch{apply(data)}}finally{setBusy(false)}
 }
 async function action(path:string,id:string){setBusy(true);setError('');try{apply(await postLocal<CleanupData>(`/api/cleanup/${account}/${path}`,path==='protect'?{message_id:id,scope:'message'}:{message_id:id}));setNotice(path==='protect'?'Message protected from future cleanup.':'Status checked. No message was moved.')}catch(e){setError(e instanceof Error?e.message:'Unable to update cleanup')}finally{setBusy(false)}}
 async function propose(operation:'trash'|'restore',id:string){setBusy(true);setError('');try{setPreview(await postLocal<ActionPreview>(`/api/cleanup/${account}/${operation}-preview`,{message_id:id}))}catch(e){setError(e instanceof Error?e.message:'Unable to prepare action')}finally{setBusy(false)}}
 return <section className={`v5-mail-section v5-cleanup ${compact?"pm-settings-section pm-compact-cleanup":""}`} aria-label="Automatic Cleanup">{dialog}<div className="v5-section-header"><div><h2>Automatic Cleanup</h2><span>{account==='personal'?'Personal Gmail':'University Gmail'} · future ads only</span></div>{!compact&&<AccountSelect account={account} onChange={onAccountChange}/>}</div><div className="v5-cleanup-body">
 {!data&&!error&&<p role="status">Loading…</p>}{data&&<><div className="v5-setting-row"><label className="v5-confirm-check v5-toggle"><input type="checkbox" checked={enabled} disabled={busy} onChange={e=>{if(!e.target.checked){setEnabled(false);void save(false,data?.policy.mode??mode,true)}else setEnabled(true)}}/>Enable automatic promotional cleanup</label><span className="v5-status-badge">{data.policy.enabled?`On · ${data.policy.mode==='automatic'?'Automatic':'Review only'}`:'Off'}</span></div>
 <fieldset><legend>Mode</legend><label className="v5-confirm-check"><input type="radio" name="cleanup-mode" checked={mode==='review'} disabled={busy} onChange={()=>{setMode('review');if(data.policy.enabled&&data.policy.mode==='automatic')void save(true,'review',true)}}/>Review only</label><label className="v5-confirm-check"><input type="radio" name="cleanup-mode" checked={mode==='automatic'} disabled={busy||!data.gmail_modify} onChange={()=>setMode('automatic')}/>Move ads to Trash</label></fieldset>
 {!data.gmail_modify&&<p className="v5-actions-note">Read only · review mode available.</p>}
 {data.policy.paused_reason&&<p role="status" className="v5-mailbox-feedback v5-cleanup-paused">{data.policy.paused_reason}</p>}
 {(enabled!==data.policy.enabled||mode!==data.policy.mode||exclusions!==data.policy.exclusions.join('\n'))&&<p className="v5-actions-note">Changes not saved.</p>}
 <div className="v5-preview-buttons"><button className="v5-small-button primary" disabled={busy||(enabled&&mode==='automatic'&&!data.gmail_modify)} onClick={()=>void save()}>Save cleanup preferences</button></div>
 <div className="v5-preview-buttons"><button className="v5-small-button" onClick={()=>navigate({page:'cleanup-review',account})}>Open Cleanup Review</button><button className="v5-small-button" onClick={()=>navigate({page:'cleanup-review',account,filter:'history'})}>Open Cleanup History</button></div>
 <details className="v5-technical-details pm-cleanup-advanced"><summary>Advanced cleanup</summary><p>Cleaned today: {data.history.cleaned_today} <small>({data.history.timezone})</small></p><details className="v5-technical-details"><summary>Protected senders</summary><label className="v5-field-label">Never clean these senders<textarea aria-label="Protected cleanup senders" value={exclusions} maxLength={12000} rows={3} onChange={e=>setExclusions(e.target.value)} placeholder="One email address per line"/></label></details>
 <details className="v5-technical-details"><summary>Review recent actions</summary><button className="v5-small-button" disabled={busy} onClick={()=>setRevision(value=>value+1)}>Refresh cleanup history</button>{!data.history.items.some(item=>item.status!=='skipped')&&<p>No recent cleanup actions. Only new mail received after enabling is considered.</p>}{data.history.items.filter(item=>item.status!=='skipped').map(item=><article className="v5-cleanup-history" key={item.message_id}><div><button className="v5-message-link" disabled={!item.subject} onClick={()=>onOpen({id:item.message_id,account_id:account,sender:item.sender??'',subject:item.subject??''})}><strong>{item.subject??'Message unavailable'}</strong><span>{item.sender}</span></button><small>{item.status==='review'&&item.label_ids?.includes('TRASH')?'In Trash':item.status==='succeeded'&&item.label_ids&&!item.label_ids.includes('TRASH')?'No longer in Trash':labels[item.status]??'Needs review'} · {new Date(item.updated_at+'Z').toLocaleString()}</small><details className="v5-technical-details"><summary>Why this action?</summary><p>{item.detail.reason}</p></details></div><div className="v5-preview-buttons">{item.label_ids?.includes('TRASH')&&<button className="v5-small-button" disabled={busy} onClick={()=>void propose('restore',item.message_id)}>Restore</button>}{item.status==='review'&&!item.label_ids?.includes('TRASH')&&<button className="v5-small-button" disabled={busy} onClick={()=>void propose('trash',item.message_id)}>Review Trash</button>}{['uncertain','running'].includes(item.status)&&<button className="v5-small-button" disabled={busy} onClick={()=>void action('reconcile',item.message_id)}>Check status</button>}<button className="v5-small-button" disabled={busy} onClick={()=>void action('protect',item.message_id)}>Keep this message</button></div></article>)}</details>
 <details className="v5-technical-details"><summary>Details</summary><p>Up to 10 new messages per sync. Exclusions and uncertain classifications are protected. No permanent deletion or AI requests. Disabling stops future actions; an already-started request may finish.</p><pre>{JSON.stringify(data.history.audit,null,2)}</pre></details>
 </details></>}{preview&&<ActionApproval key={preview.id} preview={preview} onChanged={()=>{setRevision(value=>value+1);onChanged()}} onClose={()=>setPreview(null)}/>}{notice&&<p role="status" className="v5-mailbox-feedback">{notice}</p>}{error&&<p role="alert" className="v5-mailbox-feedback error">{error}</p>}</div></section>
}
