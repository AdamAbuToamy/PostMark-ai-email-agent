import { useEffect, useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import type { AccountId, EmailMessage } from '../api/emails'
import { readLocal } from '../api/mailboxes'
import { actionApi, postLocal, type ActionPreview } from '../api/actions'
import { OrganizationPanel } from './OrganizationPanel'
import { ClassificationReview } from './ClassificationReview'
import { MailboxMessageRow } from './MailboxMessageRow'
import { AccountSelect } from './AccountSelect'
import { ActionApproval } from './ActionApproval'
interface ReviewItem { id: number; status: string; reason: string; message_id: string; message: { sender: string; subject: string; internal_date_ms?: number; label_ids?: string[] } }
export function Review({ account, onAccountChange, onOpen, onChanged }: { account: AccountId; onAccountChange: (account: AccountId) => void; onOpen: (message: EmailMessage) => void; onChanged: () => void }) {
  const [counts, setCounts] = useState<Record<string,number>>({})
  const [progress, setProgress] = useState<{unclassified:number;needs_review:number;untouched?:number;review_required?:number;protected_uncertain?:number;classified:number;history:{completed:boolean}} | null>(null)
  const [coverage, setCoverage] = useState<{unknown_labels:number} | null>(null)
  const [rows, setRows] = useState<ReviewItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [tab, setTab] = useState(new URLSearchParams(window.location.search).get('review')==='individual'?'category':new URLSearchParams(window.location.search).get('review')==='spam'?'spam':'groups')
  const history = tab === 'history'
  const [preview, setPreview] = useState<ActionPreview | null>(null)
  const [next, setNext] = useState<number | null>(null)
  async function loadMore(after: number, append: boolean) {
    try { const result = await readLocal<{ counts: Record<string,number>; coverage: {unknown_labels:number}; items: ReviewItem[]; next_after: number | null }>(`/api/review/${account}?after=${after}&queue=${tab === 'category' || tab === 'groups' ? 'all' : tab}`); setRows(previous => append ? [...previous, ...result.items] : result.items); setNext(result.next_after); setLoaded(true); setError('') }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to load review') }
  }
  useEffect(() => {
    const controller = new AbortController()
    readLocal<{ counts: Record<string,number>; coverage: {unknown_labels:number}; items: ReviewItem[]; next_after: number | null }>(`/api/review/${account}?queue=${tab === 'category' || tab === 'groups' ? 'all' : tab}`, controller.signal).then(result => { if (!controller.signal.aborted) { setRows(result.items); setCounts(result.counts); setCoverage(result.coverage); setNext(result.next_after); setLoaded(true); setError('') } }).catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load reviews') })
    readLocal<{unclassified:number;needs_review:number;untouched?:number;review_required?:number;protected_uncertain?:number;classified:number;history:{completed:boolean}}>(`/api/classifications/${account}/progress`, controller.signal).then(result => { if (!controller.signal.aborted) setProgress(result) }).catch(() => { if (!controller.signal.aborted) setProgress(null) })
    return () => controller.abort()
  }, [account, revision, tab])
  function changed() { onChanged(); setRevision(value => value + 1) }
  async function dismiss(row: ReviewItem) {
    try { await postLocal(`/api/review/${account}/${row.id}/decision`, { decision: 'dismissed', reason: 'User reviewed and dismissed this placement proposal in Postmark' }); changed() }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to record decision') }
  }
  return <section className="v5-mail-section v5-review"><div className="v5-section-header"><div><h2><ShieldAlert size={18} />A second look</h2><span>Only decisions that need your attention</span></div><AccountSelect account={account} onChange={onAccountChange} /></div><div className="v5-mailbox-tabs" role="group" aria-label="Review views">{[['groups','Grouped decisions'],['category','Category Review'],['spam','Spam'],['trash','Trash'],['history','Decision history']].map(([id, title]) => <button key={id} aria-label={title} aria-pressed={tab === id} onClick={() => setTab(id)}>{title}{counts[id] !== undefined && <span className="v5-tab-count" aria-label={`${counts[id]} pending`}>{counts[id]}</span>}</button>)}</div>
    <details className="v5-technical-details"><summary>Local coverage</summary>{progress ? <p>{progress.classified} classified · {progress.unclassified} unclassified · {progress.review_required??progress.needs_review} require review · {(progress.protected_uncertain??0)+(progress.untouched??0)} retained without action. {progress.history.completed ? 'Historical sync completed locally.' : 'Historical sync is incomplete or has not started.'} Reviewed uncertainty can remain in these totals.</p> : <p>Coverage could not be verified. No assumption is made about historical completeness.</p>}</details>
    {tab === 'groups' && <OrganizationPanel key={account} account={account} onOpen={onOpen} onChanged={changed}/>}
    {(tab === 'category'  || history) && <ClassificationReview key={`${account}-${tab}`} account={account} history={history} onOpen={onOpen} onChanged={changed} />}
    {tab !== 'category' && tab !== 'groups' && <>
    {!history && coverage && <p className="v5-actions-note">Local review proposals only · {coverage.unknown_labels} messages have unknown Gmail labels. Historical sync or review scanning may be incomplete; an empty queue does not prove all mail was checked.</p>}
    {error && <p role="alert" className="v5-mailbox-feedback error">{error}</p>}{!loaded && !error && <p className="v5-empty" role="status">Loading review…</p>}
    {preview && <ActionApproval key={preview.id} preview={preview} onChanged={changed} onClose={() => setPreview(null)} />}
    {loaded && rows.filter(row => history ? row.status !== 'proposed' : row.status === 'proposed' && row.message.label_ids?.includes(tab === 'trash' ? 'TRASH' : 'SPAM')).length === 0 && <div className="v5-empty">{history ? 'No decisions recorded yet.' : 'No pending placement proposals in the loaded messages.'}</div>}
    {rows.filter(row => history ? row.status !== 'proposed' : row.status === 'proposed' && row.message.label_ids?.includes(tab === 'trash' ? 'TRASH' : 'SPAM')).map(row => <article key={row.id} className="v5-review-row v5-placement-row"><div><MailboxMessageRow message={{ ...row.message, id:row.message_id, account_id:account, gmail_location:row.message.label_ids?.includes('TRASH') ? 'In Trash' : row.message.label_ids?.includes('SPAM') ? 'In Spam' : null }} onOpen={onOpen} needsReview={row.status === 'proposed'} /><details className="v5-technical-details"><summary>Why review?</summary><p>{row.reason}</p></details><small>{row.status === 'proposed' ? 'Not verified · human review required' : row.status.replaceAll('_', ' ')}</small></div>{row.status === 'proposed' && <div className="v5-preview-buttons"><button className="v5-small-button" onClick={() => void actionApi.preview(account, 'restore', [row.message_id]).then(setPreview).catch(err => setError(err instanceof Error ? err.message : 'Unable to preview restore'))}>Review Restore…</button><button className="v5-small-button" onClick={() => void dismiss(row)}>Dismiss proposal</button></div>}</article>)}
    {next !== null && <button className="v5-small-button" onClick={() => void loadMore(next, true)}>Load more proposals</button>}
  </>} </section>
}
