import { useEffect, useRef, useState } from 'react'
import type { AccountId, EmailMessage } from '../api/emails'
import { readLocal } from '../api/mailboxes'
import { MailboxMessageRow } from './MailboxMessageRow'
export interface ChatMessage { message_id:string; account_id:AccountId; sender:string; subject:string; internal_date_ms?:number }
export interface ChatSearchPage { messages?:ChatMessage[]; matched_count?:number; offset?:number; next_offset?:number|null; query?:Record<string,string|number|null>; coverage?:string }
export function ChatSearchResults({ account, result, onOpen, onSelect }: { account:AccountId; result:ChatSearchPage; onOpen:(message:EmailMessage)=>void; onSelect:(message:ChatMessage)=>void }) {
  const [more,setMore]=useState<ChatMessage[]>([])
  const [next,setNext]=useState(result.next_offset??null)
  const [count,setCount]=useState(result.matched_count)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const controller=useRef<AbortController|null>(null)
  useEffect(()=>()=>controller.current?.abort(),[])
  async function loadMore(){
    if(next===null||busy||!result.query)return
    const query=new URLSearchParams()
    for(const [key,value] of Object.entries(result.query))if(value!==null)query.set(key,String(value))
    query.set('offset',String(next));setBusy(true);setError('')
    controller.current=new AbortController()
    try{const page=await readLocal<ChatSearchPage>(`/api/chat/${account}/messages?${query}`,controller.current.signal);if(!controller.current.signal.aborted){setMore(previous=>[...previous,...(page.messages??[])]);setNext(page.next_offset??null);setCount(page.matched_count)}}catch(err){if(!controller.current.signal.aborted)setError(err instanceof Error?err.message:'Unable to load matching messages')}finally{if(!controller.current.signal.aborted)setBusy(false)}
  }
  const rows=[...new Map([...(result.messages??[]),...more].map(message=>[message.message_id,message])).values()]
  return <div className="v5-chat-search-results" aria-label="Local message results"><p className="v5-actions-note" role="status">{count===undefined?`${rows.length} loaded matching messages`: `${count} matching messages · showing ${rows.length}`}</p><small>{account==='personal'?'Personal Gmail':'University Gmail'} · selected account only</small>{rows.length===0&&<p>No matching local messages in this account. Check the selected account or try another sender/subject.</p>}{rows.map(message=><div className="v5-chat-message" key={message.message_id}><MailboxMessageRow message={{...message,id:message.message_id}} onOpen={onOpen}/><button className="v5-small-button" onClick={()=>onSelect(message)}>Use in next request</button></div>)}{next!==null&&<button className="v5-small-button" disabled={busy} onClick={()=>void loadMore()}>{busy?'Loading matches…':'Load more matching messages'}</button>}{error&&<p role="alert">{error}</p>}{result.coverage&&<details className="v5-technical-details"><summary>Search coverage</summary><p>{result.coverage}</p></details>}</div>
}
