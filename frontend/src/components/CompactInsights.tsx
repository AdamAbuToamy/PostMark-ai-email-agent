import { useEffect, useState } from 'react'
import { ChevronDown, Sparkles } from 'lucide-react'
import type { AccountId, EmailMessage } from '../api/emails'
import { AIInsights } from './AIInsights'
import { SavedInsights } from './SavedInsights'
export function CompactInsights({ account, messages, onOpen }: { account: AccountId; messages: EmailMessage[]; onOpen: (message: EmailMessage) => void }) {
  const storage = `postmark:insights:${account}`
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(storage) === 'open' } catch { return false } })
  const [revision, setRevision] = useState(0)
  const [count, setCount] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    fetch(`/api/insights/${account}`, { signal: controller.signal }).then(response => response.ok ? response.json() : []).then(rows => { if (!controller.signal.aborted) setCount(rows.length) }).catch(() => {})
    return () => controller.abort()
  }, [account, revision])
  return <details className="v5-compact-insights" open={open} onToggle={event => { const next = event.currentTarget.open; setOpen(next); try { localStorage.setItem(storage, next ? 'open' : 'closed') } catch { /* Storage is optional. */ } }}>
    <summary><Sparkles size={16} /><strong>AI & insights</strong><span>{count} saved · explicit approval for analysis</span><ChevronDown size={15} /></summary>
    {open && <div className="v5-compact-insights-body"><AIInsights account={account} messages={messages} onOpen={onOpen} onAnalyzed={() => setRevision(value => value + 1)} /><SavedInsights key={revision} account={account} messages={messages} onOpen={onOpen} /></div>}
  </details>
}
