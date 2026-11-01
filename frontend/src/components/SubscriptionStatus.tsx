import { useState } from 'react'
import type { Subscription } from '../api/subscriptions'
import type { AccountId } from '../api/emails'
import { readLocal } from '../api/mailboxes'

export function SubscriptionStatus({item}:{item:Subscription}) {
 return <span className={`v5-status-badge ${item.tracking?.later_message?'reappeared':['unsubscribed','manual_completed'].includes(item.tracking?.state??'')?'confirmed':''}`}>{item.tracking?.label??'Active'}{item.tracking?.later_message?' · New mail received':''}</span>
}
export function SubscriptionHistory({account,item}:{account:AccountId;item:Subscription}) {
 const [history,setHistory]=useState<unknown>(null)
 return <details className="v5-technical-details" onToggle={e=>{if(e.currentTarget.open&&history===null)void readLocal(`/api/subscriptions/${account}/${item.id}/history`).then(setHistory).catch(()=>setHistory('History unavailable'))}}><summary>Cancellation history</summary>{item.tracking?.completed_at&&<p>Confirmed {new Date(item.tracking.completed_at+'Z').toLocaleString()} · {item.tracking.method}</p>}<p>{item.tracking?.evidence}</p><pre>{history===null?'Loading…':JSON.stringify(history,null,2)}</pre></details>
}
