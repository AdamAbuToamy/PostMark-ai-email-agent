import {useEffect,useState} from 'react'
import type {AccountId} from '../api/emails'
import {readLocal} from '../api/mailboxes'
interface Progress {unassigned_waiting:number;providers:{provider:string;reasons:string[];routable:number;last_provider_backlog:number}[]}
export function OrganizationProgress({account}:{account:AccountId}){
 const [open,setOpen]=useState(false),[value,setValue]=useState<Progress|null>(null),[error,setError]=useState(false)
 useEffect(()=>{if(!open)return;let stopped=false;let timer:ReturnType<typeof setTimeout>;async function update(){try{const next=await readLocal<Progress>(`/api/hybrid/${account}/routing`,AbortSignal.timeout(8000));if(!stopped){setValue(next);setError(false)}}catch{if(!stopped)setError(true)}finally{if(!stopped)timer=setTimeout(()=>void update(),30000)}}void update();return()=>{stopped=true;clearTimeout(timer)}},[account,open])
 return <details className="pm-routing-details" onToggle={e=>setOpen(e.currentTarget.open)}><summary>Routing diagnostics</summary>{error&&<small>Diagnostics temporarily unavailable.</small>}{value&&<>{value.providers.map(p=><p key={p.provider}><strong>{p.provider}</strong>: {p.routable?'Eligible for proven tasks':p.reasons.map(r=>r.replaceAll('_',' ')).join(' · ')} · {p.last_provider_backlog} after last attempt</p>)}<small>{value.unassigned_waiting} shared tasks; last provider does not reserve them.</small></>}</details>
}
