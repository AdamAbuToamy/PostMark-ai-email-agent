import { useEffect, useState } from 'react'
import { Check, ChevronRight, FolderOpen, Lightbulb, RefreshCw, X } from 'lucide-react'
import type { AccountId } from '../api/emails'
import { mailboxApi, readLocal, type Category } from '../api/mailboxes'
import { getCategoryIcon } from './categoryIcons'
import { AccountSelect } from './AccountSelect'
interface Proposal { id: number; name: string; reason: string; status: string; icon_key: string }
interface Props { account: AccountId; onAccountChange: (account: AccountId) => void; onCategoriesChanged: (categories: Category[]) => void; onOpen: (id: string) => void; revision: number }

export function CategoryManagement({ account, onAccountChange, onCategoriesChanged, onOpen, revision }: Props) {
  const [result, setResult] = useState<{ key: string; categories: Category[]; proposals: Proposal[] } | null>(null)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)
  const [notice, setNotice] = useState('')
  const [retry, setRetry] = useState(0)
  const key = `${account}:${revision}:${retry}`
  useEffect(() => {
    const controller = new AbortController()
    Promise.all([mailboxApi.categories(account, controller.signal), readLocal<Proposal[]>('/api/categories/proposals', controller.signal)]).then(([categories, proposals]) => {
      if (!controller.signal.aborted) { setResult({ key, categories, proposals }); setError('') }
    }).catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load categories') })
    return () => controller.abort()
  }, [account, key])
  async function review(proposal: Proposal, action: 'approve' | 'reject') {
    if (busyId !== null) return
    setBusyId(proposal.id); setError(''); setNotice('')
    try {
      const response = await fetch(`/api/categories/proposals/${proposal.id}/${action}`, { method: 'POST', headers: { 'X-Email-Agent-Action': 'category-review' } })
      if (!response.ok) throw new Error(`Unable to ${action} suggestion (${response.status}). Please reload and try again.`)
      const [categories, proposals] = await Promise.all([mailboxApi.categories(account), readLocal<Proposal[]>('/api/categories/proposals')])
      setResult({ key, categories, proposals }); onCategoriesChanged(categories)
      setNotice(action === 'approve' ? `${proposal.name} is ready. Find it in navigation.` : `${proposal.name} suggestion dismissed.`)
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to save decision') }
    finally { setBusyId(null) }
  }
  const pending = result?.proposals.filter(proposal => proposal.status === 'pending') ?? []
  const loading = result?.key !== key
  return <section className="v5-category-settings" aria-label="Category management">
    <div className="v5-settings-intro"><div><h2>Your categories</h2></div><AccountSelect account={account} onChange={onAccountChange} /></div>
    {notice && <p className="v5-mailbox-feedback" role="status">{notice}</p>}
    {error && <div className="v5-mailbox-feedback error" role="alert">{error}<button className="v5-small-button" onClick={() => setRetry(value => value + 1)}>Try again</button></div>}
    {loading && !error ? <div className="v5-empty" role="status"><RefreshCw className="v5-spinning" size={20} />Loading categories…</div> : result && <>
      <div className="v5-settings-section-title"><h3><FolderOpen size={17} />Active categories</h3><span>{result.categories.length}</span></div>
      <div className="v5-category-grid">{result.categories.map(category => {
        const Icon = getCategoryIcon(category.icon_key)
        return <button className="v5-category-tile" key={category.id} onClick={() => onOpen(category.id)}><span className="v5-category-icon"><Icon size={21} strokeWidth={1.6} /></span><span><strong>{category.name}</strong><small title="New means not marked Done; organization progress is shown above.">{category.counts ? `${category.counts.all} ${category.counts.all === 1 ? 'message' : 'messages'} · ${category.counts.new} new` : 'Ready to organize'}</small></span><ChevronRight size={16} /></button>
      })}</div>
      {pending.length>0&&<><div className="v5-settings-section-title"><h3><Lightbulb size={17} />Other suggestions</h3><span>{pending.length ? `${pending.length} to review` : 'All caught up'}</span></div>
      <div className="v5-suggestion-list">{pending.length === 0 ? <div className="v5-suggestion-empty"><Check size={19} /><div><strong>You’re all set.</strong><p>New category suggestions will appear here for you to review.</p></div></div> : pending.map(proposal => {
        const Icon = getCategoryIcon(proposal.icon_key)
        return <article className="v5-suggestion" key={proposal.id}><span className="v5-category-icon"><Icon size={21} /></span><div className="v5-suggestion-copy"><strong>{proposal.name}</strong><p>{proposal.reason}</p></div><div className="v5-suggestion-actions"><button className="v5-small-button primary" disabled={busyId !== null} onClick={() => void review(proposal, 'approve')}><Check size={14} />{busyId === proposal.id ? 'Saving…' : 'Approve'}</button><button className="v5-small-button" disabled={busyId !== null} onClick={() => void review(proposal, 'reject')}><X size={14} />Dismiss</button></div></article>
      })}</div></>}
    </>}
  </section>
}
