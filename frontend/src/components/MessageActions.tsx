import { useEffect, useState, type FormEvent } from 'react'
import { Check, CheckCheck, ChevronDown, FolderInput, Undo2 } from 'lucide-react'
import type { AccountId } from '../api/emails'
import { mailboxApi, type Category, type MessageDecision } from '../api/mailboxes'
import { getCategoryIcon } from './categoryIcons'
import { GmailActions } from './GmailActions'

export function MessageActions({ account, messageId, onChanged, revision }: { account: AccountId; messageId: string; onChanged: () => void; revision: number }) {
  const [decision, setDecision] = useState<MessageDecision | null>(null)
  const [categories, setCategories] = useState<Category[]>([])
  const [choice, setChoice] = useState('')
  const [search, setSearch] = useState('')
  const [applicationRef, setApplicationRef] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    Promise.all([mailboxApi.details(account, messageId, controller.signal), mailboxApi.categories(account, controller.signal)]).then(([details, active]) => {
      if (controller.signal.aborted) return
      setDecision(details); setCategories(active); setChoice(details.category_id ?? ''); setError('')
    }).catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load actions') })
    return () => controller.abort()
  }, [account, messageId, retry, revision])

  async function toggleStatus() {
    if (!decision || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await mailboxApi.status(account, messageId, decision.local_state === 'done' ? 'new' : 'done')
      setDecision({ ...decision, local_state: result.local_state }); onChanged()
      setNotice(result.local_state === 'done' ? 'Marked Done. You can find this message in Done.' : 'Returned to New.')
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to save status') }
    finally { setBusy(false) }
  }
  async function move(event: FormEvent) {
    event.preventDefault()
    if (!decision || !choice || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await mailboxApi.move(account, messageId, choice, applicationRef)
      setDecision(result); onChanged(); setNotice(`Moved to ${categories.find(category => category.id === choice)?.name ?? 'category'}.`)
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to change category') }
    finally { setBusy(false) }
  }
  return <div className="v5-local-actions" aria-label="Local message actions">
    {error && <p className="v5-mailbox-feedback error" role="alert">{error}{!decision && <button className="v5-small-button" onClick={() => setRetry(value => value + 1)}>Try again</button>}</p>}
    {notice && <p className="v5-mailbox-feedback" role="status">{notice}</p>}
    {!decision && !error && <p className="v5-actions-note" role="status">Loading message actions…</p>}
    {decision && <>
      <div className="v5-action-primary"><span className={`v5-status-badge ${decision.local_state === 'done' ? 'done' : ''}`}>{decision.local_state === 'done' ? <CheckCheck size={13} /> : null}{decision.local_state === 'done' ? 'Done' : 'New'}</span><button className="v5-small-button" disabled={busy} onClick={() => void toggleStatus()}>{decision.local_state === 'done' ? <Undo2 size={14} /> : <Check size={14} />}{decision.local_state === 'done' ? 'Return to New' : 'Mark Done'}</button></div>
      <details className="v5-category-picker"><summary><FolderInput size={16} />Change category<ChevronDown size={14} /></summary><form onSubmit={event => void move(event)}>
        <label className="v5-field-label" htmlFor="category-search">Find a category</label><input id="category-search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search categories…" />
        <div className="v5-category-choices" role="group" aria-label="Choose category">{categories.filter(category => category.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(category => {
          const Icon = getCategoryIcon(category.icon_key)
          return <button type="button" key={category.id} aria-pressed={choice === category.id} disabled={busy} onClick={() => setChoice(category.id)}><Icon size={17} /><span>{category.name}</span>{choice === category.id && <Check size={15} />}</button>
        })}{categories.filter(category => category.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).length === 0 && <p>No categories match your search.</p>}</div>
        {choice === 'job_applications' && <label className="v5-field-label">Submitted application reference<input required maxLength={200} value={applicationRef} onChange={event => setApplicationRef(event.target.value)} placeholder="Employer, role and submission date" /></label>}
        <button className="v5-small-button primary" type="submit" disabled={busy || !choice || choice === decision.category_id}>{busy ? 'Saving…' : 'Move message'}</button>
      </form></details>
      <details className="v5-technical-details"><summary>Classification details</summary><dl><dt>Category</dt><dd>{categories.find(category => category.id === decision.category_id)?.name ?? 'Unassigned'}</dd><dt>Decision</dt><dd>{decision.source === 'manual' ? 'Your selection' : 'Local classification'}</dd><dt>Reason</dt><dd>{decision.reason ?? 'No recorded decision'}</dd><dt>Rule version</dt><dd>{decision.rule_version ?? '—'}</dd><dt>Gmail labels</dt><dd>{decision.label_ids?.join(', ') || (decision.label_ids ? 'No labels' : 'Not yet known')}</dd></dl></details>
    </>}
    <GmailActions account={account} messageId={messageId} labels={decision?.label_ids} onChanged={onChanged} />
  </div>
}
