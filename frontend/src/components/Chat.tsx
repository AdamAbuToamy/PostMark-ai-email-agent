import { useConfirmation } from './ConfirmAction'
import { useEffect, useState, useRef } from 'react'
import { useRoute } from '../routing'
import { chatSessionHeaders } from '../api/chatSession'
import { MessageSquare, Send, X } from 'lucide-react'
import type { AccountId, EmailMessage } from '../api/emails'
import { readLocal, type Category } from '../api/mailboxes'
import { postLocal, type ActionPreview } from '../api/actions'
import { AccountSelect } from './AccountSelect'
import { ChatSearchResults, type ChatMessage, type ChatSearchPage } from './ChatSearchResults'
import type { ChatSettings, Conversation } from '../api/chat'
import { ActionApproval } from './ActionApproval'
type ToolMessage = ChatMessage
interface ToolResult extends ChatSearchPage { tool?: string; status: string; message?: string; messages?: ToolMessage[]; categories?: Category[]; preview?: ActionPreview; reviews?: { reason: string }[]; subscriptions?: { identity: string; display_name?:string; observed_count: number; reason: string }[] }
interface Turn { id: number; role: string; content: { text: string; diagnostic?:unknown; processor?:string; ai_needed?:boolean; agent_requests?:number; estimated_input_tokens?:number; token_estimate_note?:string; results?: ToolResult[] } }
interface Props { account: AccountId; onAccountChange: (account: AccountId) => void; onOpen: (message: EmailMessage) => void; onChanged: () => void }
export function Chat({ account, onAccountChange, onOpen, onChanged }: Props) {
 const {confirm,dialog}=useConfirmation()
  const {navigate}=useRoute()
  const scroll=useRef<HTMLDivElement|null>(null)
  const [history, setHistory] = useState<Turn[]>([])
  const [loaded, setLoaded] = useState(false)
  const [text, setText] = useState('')
  const [settings,setSettings]=useState<ChatSettings|null>(null)
  const [sessions,setSessions]=useState<Conversation[]>([])
  const [requestId,setRequestId]=useState(()=>crypto.randomUUID())
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [context, setContext] = useState<ToolMessage | null>(null)
  const [tool, setTool] = useState('')
  const [args, setArgs] = useState('{}')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    readLocal<Turn[]>(`/api/chat/${account}/history`, controller.signal).then(rows => { if (!controller.signal.aborted) { setHistory(rows); setLoaded(true); setError('') } }).catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load chat') })
    readLocal<ChatSettings>(`/api/chat/${account}/settings`,controller.signal).then(value=>{if(!controller.signal.aborted)setSettings(value)}).catch(()=>{if(!controller.signal.aborted)setError('Unable to load AI preferences; local commands remain available')})
    readLocal<Conversation[]>(`/api/chat/${account}/conversations`,controller.signal).then(value=>{if(!controller.signal.aborted)setSessions(value)}).catch(()=>{})
    return () => controller.abort()
  }, [account, retry])
  useEffect(()=>{
    if(settings?.save_history!==false)return
    const end=()=>{void fetch(`/api/chat/${account}/session/end`,{method:'POST',headers:{...chatSessionHeaders(),'X-Email-Agent-Action':'message-management'},keepalive:true})}
    window.addEventListener('pagehide',end)
    return()=>window.removeEventListener('pagehide',end)
  },[account,settings?.save_history])
  useEffect(()=>{if(scroll.current)scroll.current.scrollTop=scroll.current.scrollHeight},[history])
  async function send(event: React.FormEvent) {
    event.preventDefault()
    if (busy || !text.trim()) return
    setBusy(true); setError('')
    const aiApproved = false
    try {
      const tool_call = tool ? { tool, args: JSON.parse(args) as object } : null
      const reply = await postLocal<{ai_needed?:boolean}>(`/api/chat/${account}`, { text, mode: tool ? 'local' : 'auto', request_id:requestId, ai_approved: aiApproved, context_ids: context ? [context.message_id] : [], tool_call })
      setHistory(await readLocal<Turn[]>(`/api/chat/${account}/history`)); setText(''); setContext(null); setConsent(false); setRequestId(crypto.randomUUID()); setSessions(await readLocal<Conversation[]>(`/api/chat/${account}/conversations`)); if(reply.ai_needed) setSettings(await readLocal<ChatSettings>(`/api/chat/${account}/settings`))
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to send message') }
    finally { setBusy(false) }
  }
  async function enable() {
    setBusy(true); setError('')
    try { setSettings(await postLocal<ChatSettings>(`/api/chat/${account}/settings`,{enabled:true,developer_local:false,cost_approved:consent}));setConsent(false) }
    catch(err){setError(err instanceof Error?err.message:'Unable to enable Gemini')}
    finally{setBusy(false)}
  }
  async function conversation(operation:'new'|'clear'|'clear_all'|'select',conversation_id?:string) {
    if((operation==='clear'||operation==='clear_all')&&!(await confirm(`Clear this local conversation for ${account} and cancel its pending approvals? Gmail is not affected.`)))return
    setBusy(true);setError('')
    try{setSessions(await postLocal<Conversation[]>(`/api/chat/${account}/conversations`,{operation,conversation_id,confirmed:operation==='clear'||operation==='clear_all'}));setContext(null);setText('');setTool('');setRequestId(crypto.randomUUID());setRetry(value=>value+1)}catch(err){setError(err instanceof Error?err.message:'Unable to change conversation')}finally{setBusy(false)}
  }
  return <section className="v5-mail-section v5-chat" aria-label="Postmark Chat">{dialog}<div className="v5-section-header"><div><h2><MessageSquare size={18} />Your mail, with a little help.</h2><span>{settings?.default_mode==='gemini' ? 'Gemini assistant · simple commands handled locally' : 'Your assistant · enable Gemini for AI interpretation'}</span></div><AccountSelect account={account} onChange={onAccountChange} /></div>
    {error && <div className="v5-mailbox-feedback error" role="alert">{error}<button className="v5-small-button" onClick={() => setRetry(value => value + 1)}>Reload chat</button></div>}
    <div className="v5-chat-toolbar"><button className="v5-small-button" disabled={busy} onClick={()=>void conversation('new')}>New chat</button><button className="v5-small-button" disabled={busy||!history.length} onClick={()=>void conversation('clear')}>Clear conversation</button><details className="v5-overflow"><summary aria-label="More chat actions">•••</summary><button className="v5-small-button" disabled={busy} onClick={()=>void conversation('clear_all')}>Clear all history</button></details><button className="v5-small-button" onClick={()=>navigate({page:'settings',account})}>Open AI Settings</button>{sessions.length>1&&<select aria-label="Conversation" disabled={busy} value={sessions.find(item=>item.active)?.id??''} onChange={event=>void conversation('select',event.target.value)}>{sessions.map(item=><option key={item.id} value={item.id}>{new Date(item.created_at+'Z').toLocaleString()} · {item.message_count} messages</option>)}</select>}</div>
    {settings?.save_history===false&&<p className="v5-actions-note" role="status">Temporary chat · this document’s conversation stays in server memory only. Closing/reloading ends it; abandoned sessions expire after 30 minutes. Provider retention is controlled separately by Google.</p>}
    {settings&&!settings.enabled&&settings.configured&&<div className="v5-chat-onboarding"><h3>Enable your Gemini assistant</h3><p>Send prompts, short conversational context and bounded sender/subject tool results to the configured Gemini service under your saved Google project policy. Confirm the provider tier in Settings; billed use requires separate approval. Simple commands stay local; bodies and attachments are not automatically sent. You can disable this in Settings.</p><label className="v5-confirm-check"><input type="checkbox" checked={consent} onChange={event=>setConsent(event.target.checked)}/>I allow Gemini processing under my saved Google project policy.</label><button className="v5-small-button primary" disabled={!consent||busy} onClick={()=>void enable()}>Enable Gemini assistant</button></div>}
    {settings&&!settings.configured&&<p className="v5-chat-usage">Gemini is not configured. Local mail searches and preview commands work without AI.</p>}
    <div ref={scroll} className="v5-chat-history" aria-live="polite">{!loaded && !error && <p role="status">Loading conversation…</p>}{loaded && !history.length && <div className="v5-chat-welcome"><h3>What would you like to organize?</h3><p>Try “List categories”, “Find university emails I haven’t marked Done”, or “Create a Travel category”. Select a search result to move it or mark it Done.</p></div>}{history.map(turn => <article key={turn.id} className={`v5-chat-turn ${turn.role}`}><strong>{turn.role === 'user' ? 'You' : 'Postmark'}</strong>{turn.role==='assistant'&&['gemini_failed','not_processed'].includes(turn.content.processor??'')&&<small className="v5-processor">{turn.content.processor==='gemini'?'Processed by Gemini':turn.content.processor==='cached_gemini'?'Reused Gemini response or plan · no new request':turn.content.processor==='gemini_failed'?'Gemini attempt failed':turn.content.processor==='not_processed'?'Not sent to Gemini · setup or limit requires attention':turn.content.processor==='local'?'Handled locally · no Gemini request':'Saved conversation · processing source not recorded'}</small>}<p dir="auto">{turn.content.text}</p>{(turn.content.processor||turn.content.agent_requests!==undefined)&&<details className="v5-technical-details"><summary>Processing details</summary><p>{turn.content.processor==='gemini'?'Processed by Gemini':turn.content.processor==='cached_gemini'?'Reused Gemini response · no new request':'Handled locally · no Gemini request'}</p><small>{turn.content.agent_requests??0} model requests this turn{turn.content.estimated_input_tokens?` · approximately ${turn.content.estimated_input_tokens} input tokens`:null}. {turn.content.token_estimate_note}</small>{!!turn.content.diagnostic&&<pre>{JSON.stringify(turn.content.diagnostic,null,2)}</pre>}</details>}{turn.content.results?.map((result, index) => <div key={index} className="v5-chat-result">
      {result.message && <p>{result.message}</p>}{result.status === 'failed' && <p role="alert">This tool did not succeed.</p>}
      {result.categories && <div className="v5-chat-category-list">{result.categories.map(category => <span key={category.id}>{category.name}</span>)}</div>}
      {result.messages && <ChatSearchResults account={account} result={result} onOpen={onOpen} onSelect={setContext} />}
      {result.reviews?.map((review, i) => <p key={i}>{review.reason} · Review the message in Needs Review before taking action.</p>)}
      {result.subscriptions?.map(subscription => <p key={subscription.identity}>{subscription.display_name ?? subscription.identity} · {subscription.observed_count} observed messages · {subscription.reason}</p>)}
      {result.preview && <ActionApproval preview={result.preview} onChanged={() => { onChanged(); setRetry(value => value + 1) }} />}
    </div>)}</article>)}</div>
    <form className="v5-chat-composer" onSubmit={event => void send(event)}>{context && <div className="v5-chat-context"><span>Selected: {context.subject}</span><button type="button" aria-label="Clear selected message" onClick={() => setContext(null)}><X size={14} /></button></div>}
      <label className="v5-field-label" htmlFor="chat-message">Message</label><textarea id="chat-message" placeholder="Ask about your selected account…" value={text} maxLength={2000} onChange={event => { setText(event.target.value); setRequestId(crypto.randomUUID()) }} required />
      <div className="v5-chat-options"><button className="v5-small-button primary" disabled={busy} type="submit"><Send size={14}/>{busy?'Working…':'Send'}</button></div>
      <details className="v5-technical-details"><summary>Developer tools</summary><label className="v5-field-label">Tool<select value={tool} onChange={event => setTool(event.target.value)}><option value="">Understand my request</option>{['search_messages','list_categories','propose_category','create_category','approve_category','assign_messages','set_status','classify_orders','review_spam','list_subscriptions'].map(value => <option key={value}>{value}</option>)}</select></label>{tool && <label className="v5-field-label">Validated JSON arguments<textarea value={args} onChange={event => setArgs(event.target.value)} /></label>}</details>
    </form></section>
}
