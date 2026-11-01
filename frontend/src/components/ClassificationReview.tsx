import { useEffect, useState } from 'react'
import type { AccountId, EmailMessage } from '../api/emails'
import { readLocal, type Category } from '../api/mailboxes'
import { postLocal } from '../api/actions'
import { MailboxMessageRow } from './MailboxMessageRow'
interface Candidate { message_id: string; sender: string; subject: string; internal_date_ms: number; category_id: string | null; gmail_location?: string | null; reason: string | null; fingerprint: string }
interface History { id: number; message_id: string; decision: string; created_at: string }
interface Data { items: Candidate[]; next_after: string | null; history: History[]; coverage: { local_messages: number; unknown_labels: number }; coverage_note: string }
export function ClassificationReview({ account, history = false, onOpen, onChanged }: { account: AccountId; history?: boolean; onOpen: (message: EmailMessage) => void; onChanged: () => void }) {
  const [attention,setAttention]=useState('required')
  const [data, setData] = useState<Data | null>(null)
  const [categories, setCategories] = useState<Category[]>([])
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [choices, setChoices] = useState<Record<string, string>>({})
  const [references, setReferences] = useState<Record<string,string>>({})
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    Promise.all([readLocal<Data>(`/api/classification-review/${account}?attention=${attention}`, controller.signal), readLocal<Category[]>(`/api/category-mailbox/${account}/categories`, controller.signal)]).then(([result, active]) => { if (!controller.signal.aborted) { setData(result); setCategories(active); setError('') } }).catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load classification review') })
    return () => controller.abort()
  }, [account, revision, attention])
  async function decide(row: Candidate, decision: string) {
    if (busy) return
    setBusy(true); setError('')
    try { await postLocal(`/api/classification-review/${account}/${encodeURIComponent(row.message_id)}/decision`, { fingerprint: row.fingerprint, decision, category_id: choices[row.message_id] ?? null, application_ref: references[row.message_id] ?? '' }); onChanged(); setRevision(value => value + 1) }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to save review') }
    finally { setBusy(false) }
  }
  async function more() { if (!data?.next_after) return; try { const next = await readLocal<Data>(`/api/classification-review/${account}?after=${encodeURIComponent(data.next_after)}&attention=${attention}`); setData({ ...next, items: [...data.items, ...next.items] }) } catch { setError('Unable to load more candidates') } }
  const names = Object.fromEntries(categories.map(category => [category.id, category.name]))
  return <div className="v5-classification-review">
    {error && <p role="alert" className="v5-mailbox-feedback error">{error}<button className="v5-small-button" onClick={() => setRevision(value => value + 1)}>Retry review</button></p>}{!data && !error && <p role="status" className="v5-empty">Loading classification review…</p>}
    {!history&&<label className="pm-review-filter">Show<select aria-label="Review scope" value={attention} onChange={e=>{setAttention(e.target.value);setData(null)}}><option value="required">Needs a decision</option><option value="untouched">Other · left untouched</option><option value="all">All uncertain classifications</option></select></label>}
    {data && !history && <details className="v5-technical-details"><summary>Coverage details</summary><p className="v5-actions-note">{data.coverage.local_messages} local messages · {data.coverage.unknown_labels} without known Gmail labels. {data.coverage_note}</p></details>}
    {data && (history ? data.history.length === 0 : data.items.length === 0) && <p className="v5-empty">{history ? 'No classification decisions recorded yet.' : 'No unresolved classification candidates in the local messages.'}</p>}
    {history ? data?.history.map(item => <div className="v5-review-row" key={item.id}><button className="v5-small-button" onClick={() => void readLocal<{ sender: string; subject: string }>(`/api/local-messages/${account}/${encodeURIComponent(item.message_id)}`).then(message => onOpen({ ...message, id: item.message_id, account_id: account })).catch(() => setError('Message unavailable'))}>Open reviewed message</button><span>{item.decision} · {item.created_at}</span></div>) : data?.items.map(row => <article className="v5-review-candidate" key={row.message_id}>
      <MailboxMessageRow message={{ ...row, id: row.message_id, account_id: account }} onOpen={onOpen} />
      <div className="v5-review-candidate-controls"><span>{row.category_id ? names[row.category_id] ?? 'Unassigned' : 'Unclassified'} · Choose the correct category</span><label className="v5-sr-only" htmlFor={`review-${row.message_id}`}>Category for {row.subject}</label><select id={`review-${row.message_id}`} value={choices[row.message_id] ?? ''} onChange={event => setChoices(previous => ({ ...previous, [row.message_id]: event.target.value }))}><option value="">Choose category…</option>{categories.map(category => <option key={category.id} value={category.id}>{category.name}</option>)}</select>{choices[row.message_id] === 'job_applications' && <input aria-label={`Submitted application reference for ${row.subject}`} placeholder="Submitted application reference" maxLength={200} value={references[row.message_id] ?? ''} onChange={event => setReferences(previous => ({ ...previous, [row.message_id]:event.target.value }))} />}<button className="v5-small-button" disabled={busy || !choices[row.message_id] || (choices[row.message_id] === 'job_applications' && !references[row.message_id]?.trim())} onClick={() => void decide(row, 'assigned')}>Assign</button>{row.category_id && !['other','job_applications'].includes(row.category_id) && <button className="v5-small-button" disabled={busy} onClick={() => void decide(row, 'approved')}>Approve suggestion</button>}<button className="v5-small-button" disabled={busy} onClick={() => void decide(row, 'rejected')}>Reject suggestion</button><button className="v5-small-button" disabled={busy} onClick={() => void decide(row, 'reviewed')}>Mark reviewed</button></div><details className="v5-technical-details"><summary>Why review?</summary><p>{row.reason?.replaceAll('_', ' ') ?? 'No category has been assigned. Human review is needed.'}</p></details>
    </article>)}
    {!history && data?.next_after && <button className="v5-small-button" onClick={() => void more()}>Load more candidates</button>}
  </div>
}
