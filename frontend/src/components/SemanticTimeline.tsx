import {isModelAnalysis} from '../processingStates'
import {useState} from 'react'
import {readLocal} from '../api/mailboxes'
import type {AccountId} from '../api/emails'
interface Entry {message_id:string;internal_date_ms:number;relationship_basis?:string;semantic:{status?:string;message_type?:string;source:string;model?:string;factuality:string}}
export function SemanticTimeline({account,id}:{account:AccountId;id:string}){
 const [entries,setEntries]=useState<Entry[]|null>(null),[error,setError]=useState('')
 return <details className="v5-technical-details" onToggle={e=>{if(e.currentTarget.open&&entries===null)void readLocal<{timeline:Entry[]}>(`/api/organizer/${account}/messages/${encodeURIComponent(id)}`).then(r=>setEntries(r.timeline)).catch(()=>setError('Message evidence unavailable'))}}><summary>Related message evidence</summary>{error&&<p role="alert">{error}</p>}{entries?.length===0&&<p>No linked evidence yet.</p>}<ul>{entries?.map(entry=><li key={entry.message_id}><time>{new Date(entry.internal_date_ms).toLocaleDateString()}</time> · {entry.semantic.status||entry.semantic.message_type||'Unknown'} · {isModelAnalysis(entry.semantic.source)?`AI interpretation · ${entry.semantic.source}${entry.semantic.model?' / '+entry.semantic.model:''}`:'Local evidence'}{entry.relationship_basis==='AI interpretation'?' · Suggested link; unverified':entry.relationship_basis==='observed'?' · Matching thread/reference':''}</li>)}</ul></details>
}
