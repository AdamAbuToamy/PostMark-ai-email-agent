import { MarkUnsubscribed } from './MarkUnsubscribed'
import { SubscriptionStatus, SubscriptionHistory } from './SubscriptionStatus'
import { useConfirmation } from './ConfirmAction'
import { useEffect, useState } from 'react'
import type { AccountId, EmailMessage } from '../api/emails'
import { readLocal } from '../api/mailboxes'
import { actionApi, postLocal, type ActionPreview } from '../api/actions'
import type { SubscriptionsData } from '../api/subscriptions'
import { SubscriptionManualActions } from './SubscriptionManualActions'
import { SubscriptionProgress } from './SubscriptionProgress'
import { AccountSelect } from './AccountSelect'
import { SubscriptionCancellation } from './SubscriptionCancellation'
import { ActionApproval } from './ActionApproval'
import { ManualUnsubscribeButton } from './ManualUnsubscribeButton'

export function Subscriptions({ account, onAccountChange, onChanged, onOpen }: { account: AccountId; onAccountChange: (account: AccountId) => void; onChanged: () => void; onOpen: (message: EmailMessage) => void }) {
 const {confirm,dialog}=useConfirmation()
  const [filter,setFilter]=useState<'active'|'pending'|'unsubscribed'|'all'>(new URLSearchParams(window.location.search).get('status')==='unsubscribed'?'unsubscribed':'active')
  const [search,setSearch]=useState('')
  const [data, setData] = useState<SubscriptionsData | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [cancellation,setCancellation]=useState<ActionPreview|null>(null)
  const [preview, setPreview] = useState<ActionPreview | null>(null)
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    readLocal<SubscriptionsData>(`/api/subscriptions/${account}`, controller.signal).then(result => { if (!controller.signal.aborted) { setData(result); setError('') } }).catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load subscriptions') })
    return () => controller.abort()
  }, [account, revision])
  async function unsubscribe(id:string){setRefreshing(true);setError('');try{setCancellation(await (()=>{const saved=data?.items.find(item=>item.id===id)?.request_state;return saved?readLocal<ActionPreview>(`/api/actions/${account}/${saved.action_id}`):postLocal<ActionPreview>(`/api/subscriptions/${account}/cancellation`,{subscription_id:id})})())}catch(e){setError(e instanceof Error?e.message:'Unsubscribe unavailable')}finally{setRefreshing(false)}}
  async function refreshHeaders() {
    if (!(await confirm(`Read mailing-list headers for up to 20 local promotional messages in ${account}? No message bodies, AI or Gmail changes are involved.`))) return
    setRefreshing(true); setError('')
    try {
      const result = await postLocal<{ items: { status: string }[] }>(`/api/subscriptions/${account}/refresh`, {})
      if (result.items.some(item => item.status === 'failed')) setError('Some headers were unavailable. Counts include only verified reads.')
      setRevision(value => value + 1)
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to refresh headers') }
    finally { setRefreshing(false) }
  }
  async function trash() {
    const ids = [...new Set(data?.items.filter(item => selected.includes(item.id)).flatMap(item => item.message_ids) ?? [])]
    if (!ids.length || ids.length > 20) { setError('Review up to 20 observed messages per batch. Use Promotions to select individual messages from larger lists.'); return }
    try { setPreview(await actionApi.preview(account, 'trash', ids, 'promotions')) }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to preview subscription mail') }
  }
  async function eligibility(ids: string[]) {
    if (!ids.length || ids.length > 5) { setError('Select 1–5 lists to verify at a time.'); return }
    if (!(await confirm(`Read up to ${ids.length} original messages and independently verify their DKIM signatures? This checks eligibility only; unsubscribe requests require another approval.`))) return
    setRefreshing(true); setError('')
    try { setPreview(await postLocal<ActionPreview>(`/api/subscriptions/${account}/preview`, { subscription_ids: ids }));setRevision(value=>value+1) }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to verify list eligibility') }
    finally { setRefreshing(false) }
  }
  const items=(data?.items??[]).filter(item=>(filter==='all'||(filter==='unsubscribed'?['unsubscribed','manual_completed'].includes(item.tracking?.state??''):filter==='active'?(item.tracking?.state??'active')==='active':!['active','unsubscribed','manual_completed'].includes(item.tracking?.state??'active')))&&item.display_name.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  return <section className="v5-mail-section">{dialog}<div className="v5-section-header"><div><h2>Subscriptions</h2><span>Manage emails you no longer want.</span></div><AccountSelect account={account} onChange={onAccountChange} /></div><details className="v5-technical-details"><summary>Details</summary><button className="v5-small-button" disabled={refreshing||data?.progress.ready===0} onClick={() => void refreshHeaders()}>{refreshing ? 'Reading headers…' : 'Read up to 20 missing list headers'}</button><button className="v5-small-button" disabled={refreshing} onClick={()=>setRevision(value=>value+1)}>Refresh status</button>{data&&<SubscriptionProgress progress={data.progress}/>}<p className="v5-review-intro">{data?.coverage ?? 'Loading observed mailing lists…'}</p></details><div className="v5-subscription-tools"><input aria-label="Search subscriptions" type="search" placeholder="Search subscriptions" value={search} onChange={e=>setSearch(e.target.value)}/><select aria-label="Subscription status" value={filter} onChange={e=>{setFilter(e.target.value as typeof filter);setSelected([])}}><option value="active">Active</option><option value="pending">Requests / needs action</option><option value="unsubscribed">Unsubscribed</option><option value="all">All</option></select></div>{data&&<p className="v5-subtle-status">{items.length} {items.length===1?'list':'lists'}{data.progress.running?' · Updating…':''}</p>}{error && <p role="alert" className="v5-mailbox-feedback error">{error}</p>}{selected.length > 0 && <div className="v5-selection-bar"><span>{selected.length} subscriptions selected</span><button className="v5-small-button" onClick={() => void trash()}>Review Trash preview…</button><button className="v5-small-button" disabled={refreshing} onClick={() => void eligibility(selected)}>Check unsubscribe eligibility…</button></div>}{cancellation&&<SubscriptionCancellation key={cancellation.id} action={cancellation} onChanged={()=>{onChanged();setRevision(value=>value+1)}} onClose={()=>setCancellation(null)}/>} {preview && <ActionApproval key={preview.id} preview={preview} onChanged={() => { onChanged(); setRevision(value => value + 1) }} onClose={() => setPreview(null)} />}{data&&items.length===0&&<div className="v5-empty"><strong>No subscriptions here.</strong><p>{search?'Try another sender or list name.':filter==='unsubscribed'?'Confirmed cancellations will appear here.':'Choose another account or check Details for discovery options.'}</p></div>}{[
      {title:'Mailing lists',items:items.filter(item=>item.kind!=='promotional_sender')},
      {title:'Other senders',items:items.filter(item=>item.kind==='promotional_sender')},
    ].map(section=>section.items.length>0&&<div className="v5-subscription-group" key={section.title}><h3>{section.title}</h3>{section.items.map(item => <article key={item.id} className="v5-subscription-row">
      <div className="v5-subscription-row-heading"><strong>{item.display_name}</strong><SubscriptionStatus item={item}/></div>
      <div className="v5-subscription-row-actions">{['unsubscribed','manual_completed'].includes(item.tracking?.state??'')&&item.request_state&&<button className="v5-small-button" disabled={refreshing} onClick={()=>void unsubscribe(item.id)}>View status</button>}{!['unsubscribed','manual_completed'].includes(item.tracking?.state??'')&&<><button className="v5-small-button primary" disabled={refreshing} onClick={()=>void unsubscribe(item.id)}>{item.tracking&&item.tracking.state!=='active'?'View status':'Unsubscribe'}</button><ManualUnsubscribeButton account={account} id={item.id} label="Open manually"/><MarkUnsubscribed account={account} id={item.id} onChanged={()=>{onChanged();setRevision(v=>v+1)}}/></>}</div>
      <details className="v5-technical-details"><summary>Technical Details</summary><SubscriptionHistory account={account} item={item}/><p><span>{item.display_status}</span> · {item.observed_count} observed messages</p><label><input type="checkbox" checked={selected.includes(item.id)} onChange={event=>setSelected(previous=>event.target.checked?[...previous,item.id]:previous.filter(id=>id!==item.id))}/>Select for advanced bulk actions</label>
      {item.request_state&&<button className="v5-small-button" onClick={()=>void readLocal<ActionPreview>(`/api/actions/${account}/${item.request_state!.action_id}`).then(value=>{if(value.kind==='unsubscribe:browser')setCancellation(value);else setPreview(value)}).catch(()=>setError('Saved request unavailable'))}>View saved request</button>}
      <button className="v5-small-button" onClick={()=>void readLocal<{sender:string;subject:string}>(`/api/local-messages/${account}/${encodeURIComponent(item.message_ids[0])}`).then(message=>onOpen({id:item.message_ids[0],account_id:account,...message})).catch(()=>setError('Message unavailable'))}>Open original message</button>
      {!item.request_state&&item.one_click_candidate&&<details className="v5-technical-details"><summary>Advanced verification</summary><button className="v5-small-button" disabled={refreshing} onClick={()=>void eligibility([item.id])}>Check unsubscribe eligibility…</button></details>}
      <details className="v5-technical-details"><summary>Advanced list information</summary><p className="v5-actions-note">{item.manual_state?'Handled locally · delivery not verified. ':''}{item.reason}</p><SubscriptionManualActions account={account} id={item.id} messageId={item.message_ids[0]} destinations={item.manual_destinations} onChanged={()=>{onChanged();setRevision(value=>value+1)}}/>
      <details className="v5-technical-details"><summary>List options and verification details</summary><p>{item.reason}</p><p>{item.methods.join(' · ') || 'No supported method observed'}</p><p className="v5-actions-note">Manual destinations are unverified plain text. Inspect the original and verify its sender before using them.</p>{item.manual_destinations?.map(value => <code className="v5-manual-destination" key={value}>{value}</code>)}<p className="v5-actions-note">{item.request_state ? `Saved request: ${item.request_state.status}. Future delivery has not been confirmed stopped. Inspect the action audit before any manual retry.` : 'Manual option: use the verified original sender’s own unsubscribe process yourself. Postmark never opens links or sends email automatically.'}</p></details></details>
    </details></article>)}</div>)}</section>
}
