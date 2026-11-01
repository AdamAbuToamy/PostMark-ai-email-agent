import { useEffect, useState } from 'react'
import { CheckCheck, Inbox, RefreshCw, Search, X } from 'lucide-react'
import type { AccountId, EmailMessage } from '../api/emails'
import { mailboxApi, readLocal, type MailboxResult, type MailboxMessage } from '../api/mailboxes'
import { SemanticOrganization } from './SemanticOrganization'
import { SubscriptionSummary } from './SubscriptionSummary'
import { AccountSelect } from './AccountSelect'
import { OtherProcessing, type OtherProgress } from './OtherProcessing'
import { processingLabel } from '../processingStates'
import { MailboxMessageRow } from './MailboxMessageRow'
import {BulkMailActions} from './BulkMailActions'

interface Props {
  account: AccountId
  categoryId: string
  title: string
  onOpen: (message: EmailMessage) => void
  onAccountChange: (account: AccountId) => void
  revision: number
  onChanged: () => void
}
export function LocalCategory({ account, categoryId, title, onOpen, onAccountChange, revision, onChanged }: Props) {
  const [selection, setSelection] = useState<string[]>([])
  const [tab, setTab] = useState<'new' | 'done' | 'all'>('new')
  const [section,setSection]=useState('all')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('newest')
  const [offset, setOffset] = useState(0)
  const [retry, setRetry] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const [response, setResponse] = useState<{ key: string; data?: MailboxResult; error?: string } | null>(null)
  const [progress, setProgress] = useState<{account:AccountId;data:OtherProgress}|null>(null)
  const params = new URLSearchParams({ state: tab, section, search, sort, offset: String(offset) })
  const url = `/api/category-mailbox/${account}/${encodeURIComponent(categoryId)}?${params}`
  const key = `${url}:${revision}:${retry}`
  const loading = response?.key !== key
  const data = response?.data
  const error = response?.key === key ? response.error : ''
  const otherProgress=progress?.account===account?progress.data:!loading?data?.processing:undefined

  useEffect(() => {
    const controller = new AbortController()
    // Debounce typing and cancel obsolete searches/account requests.
    const timer = window.setTimeout(() => {
      readLocal<MailboxResult>(url, controller.signal).then(result => {
        if (controller.signal.aborted) return
        if (offset && offset >= result.total) { setOffset(0); return }
        setResponse({ key, data: result })
      }).catch(err => {
        if (!controller.signal.aborted) setResponse({ key, error: err instanceof Error ? err.message : 'Unable to load messages' })
      })
    }, search ? 180 : 0)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [url, key, offset, search])

  useEffect(()=>{if(categoryId!=='other')return;const timer=window.setInterval(()=>{if(document.visibilityState==='visible')setRetry(v=>v+1)},30000);return()=>window.clearInterval(timer)},[categoryId,account])
  useEffect(()=>{
    if(categoryId!=='other')return
    const controller=new AbortController()
    async function refresh(){if(document.hidden)return;try{const value=await readLocal<OtherProgress>(`/api/hybrid/${account}/other`,controller.signal);if(!controller.signal.aborted)setProgress({account,data:value})}catch{/* Mailbox errors remain visible; never fabricate progress. */}}
    void refresh();const timer=window.setInterval(()=>void refresh(),30000)
    return()=>{controller.abort();window.clearInterval(timer)}
  },[categoryId,account])

  async function toggle(item: MailboxMessage) {
    if (busy) return
    setBusy(item.message_id); setActionError(''); setNotice('')
    try {
      await mailboxApi.status(account, item.message_id, item.local_state === 'done' ? 'new' : 'done')
      setNotice(item.local_state === 'done' ? 'Message returned to New.' : 'Message marked Done.')
      onChanged()
    } catch (err) { setActionError(err instanceof Error ? err.message : 'Unable to save message') }
    finally { setBusy(null) }
  }
  return <section className="v5-mail-section v5-category-mailbox" aria-label={`${title} mailbox`}>
    <div className="v5-section-header"><div><h2>Your messages</h2><span>{data ? `${data.counts.all} messages` : 'A little more organized.'}</span></div><AccountSelect account={account} onChange={onAccountChange} /></div>
    {categoryId==='other'&&otherProgress&&<OtherProcessing progress={otherProgress}/>}
    <div className="v5-mailbox-toolbar">
      {categoryId === 'promotions' && <SubscriptionSummary key={account} account={account} revision={revision} onChanged={onChanged} />}
      <div className="v5-mailbox-tabs" role="group" aria-label="Message status">{(['new', 'done', 'all'] as const).map(value => <button key={value} aria-pressed={tab === value} className={tab === value ? 'active' : ''} onClick={() => { setTab(value); setSelection([]); setOffset(0) }}>
        {value === 'new' ? 'New' : value === 'done' ? 'Done' : 'All'}<span>{data?.counts[value] ?? '—'}</span>
      </button>)}</div>
      <select className="v5-sort-select" aria-label="Sort messages" value={sort} onChange={event => { setSort(event.target.value); setOffset(0) }}><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select>
    </div>
    {categoryId==='other'&&data?.discovery?.source==='local_topics'&&<p className="v5-actions-note">Local topics · unresolved messages are still queued for understanding.</p>}
    {data?.sections&&<div className="pm-section-chips" role="group" aria-label="Smart sections"><button aria-pressed={section==='all'} onClick={()=>{setSection('all');setOffset(0);setSelection([])}}>All types</button>{data.sections.filter(item=>item.count>0||item.id===section).map(item=><button key={item.id} title={item.reason} aria-pressed={section===item.id} onClick={()=>{setSection(item.id);setOffset(0);setSelection([])}}>{item.name}<small>{item.count}</small></button>)}</div>}
    {data && data.items.filter(item=>item.section_id==='other').length>=2 && <SemanticOrganization key={`${account}-${categoryId}-${section}`} account={account} categoryId={categoryId} candidates={data.items.filter(item=>item.section_id==='other').slice(0,10).map(item=>({message_id:item.message_id,subject:item.subject}))}/>}
    <div className="v5-search-field"><Search size={17} aria-hidden="true" /><input aria-label={`Search ${title}`} placeholder="Search sender or subject…" value={search} maxLength={200} onChange={event => { setSearch(event.target.value); setSelection([]); setOffset(0) }} />{search && <button onClick={() => { setSearch(''); setSelection([]); setOffset(0) }} aria-label="Clear search"><X size={16} /></button>}</div>
    {data&&<BulkMailActions key={`${account}-${categoryId}-${tab}-${section}-${search}-${selection.join('|')}`} account={account} scope={{view:'category',category:categoryId,state:tab,search}} visible={data.items.map(i=>i.message_id)} selected={selection} setSelected={setSelection} onChanged={onChanged} allMatchingAllowed={section==='all'}/>}
    {notice && <p className="v5-mailbox-feedback" role="status">{notice}</p>}
    {actionError && <p className="v5-mailbox-feedback error" role="alert">{actionError}</p>}
    {loading ? <div className="v5-empty" role="status"><RefreshCw size={20} className="v5-spinning" />Loading messages…</div>
      : error ? <div className="v5-empty" role="alert"><p>{error}</p><button className="v5-small-button" onClick={() => setRetry(value => value + 1)}>Try again</button></div>
      : !data?.items.length ? <div className="v5-empty"><span className="v5-empty-icon">{tab === 'new' && !search ? <CheckCheck size={27} /> : <Inbox size={27} />}</span><strong>{search ? 'No matching messages' : tab === 'new' ? 'You’re all caught up' : tab === 'done' ? 'Nothing done just yet' : 'A fresh start'}</strong><p>{search ? 'Try another sender or subject.' : tab === 'new' ? 'Messages you finish will be waiting in Done.' : tab === 'done' ? 'Mark a message Done to keep it here.' : 'Messages assigned to this category will appear here. You can also try your other account.'}</p></div>
      : <div className="v5-message-list">{data.items.map(item => <div className="v5-selectable-row" key={item.message_id}><input type="checkbox" aria-label={`Select ${item.subject}`} checked={selection.includes(item.message_id)}  onChange={event => setSelection(previous => event.target.checked ? [...previous, item.message_id] : previous.filter(id => id !== item.message_id))} /><div className="pm-message-body"><MailboxMessageRow message={{ id: item.message_id, account_id: item.account_id, sender: item.sender, subject: item.subject, internal_date_ms: item.internal_date_ms }} onOpen={onOpen} localState={item.local_state} jobStatus={item.job_status} semantic={item.semantic} needsReview={item.attention_state==='review'||(!item.attention_state&&item.classification_status==='needs_review')} notice={item.gmail_location ?? item.order_notice} onToggle={() => void toggle(item)} busy={busy !== null} />{item.processing&&<small className="pm-message-processing" title={item.processing.reason}>{processingLabel[item.processing.status]??'Needs attention'}</small>}</div></div>)}</div>}
    {!loading && !error && data && data.total > 0 && <div className="v5-mailbox-pagination"><span>{offset + 1}–{Math.min(offset + data.items.length, data.total)} of {data.total}</span><div><button className="v5-small-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button><button className="v5-small-button" disabled={data.next_offset === null} onClick={() => setOffset(data.next_offset ?? 0)}>Next</button></div></div>}
  </section>
}
