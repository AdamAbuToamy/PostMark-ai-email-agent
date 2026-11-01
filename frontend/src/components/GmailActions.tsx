import { useEffect, useState } from 'react'
import { LockKeyhole, Trash2, Undo2 } from 'lucide-react'
import type { AccountId } from '../api/emails'
import { actionApi, type ActionPreview, type Capabilities } from '../api/actions'
import { useRoute } from '../routing'
import { ActionApproval } from './ActionApproval'
export function GmailActions({ account, messageId, labels, onChanged }: { account: AccountId; messageId: string; labels?: string[] | null; onChanged: () => void }) {
  const { navigate } = useRoute()
  const [preview, setPreview] = useState<ActionPreview | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  useEffect(() => { actionApi.capabilities(account).then(setCapabilities).catch(() => {}) }, [account])
  async function propose(operation: 'trash' | 'restore') {
    setBusy(true); setError('')
    try { setPreview(await actionApi.preview(account, operation, [messageId])) }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to build preview') }
    finally { setBusy(false) }
  }
  return <details className="v5-technical-details"><summary><LockKeyhole size={13} />Gmail actions <span>{capabilities?.gmail_modify ? 'Write enabled' : 'Read only'}</span></summary><p className="v5-actions-note">Every Gmail action needs a separate preview and confirmation. Local New / Done never changes Gmail read status.</p><div className="v5-preview-buttons"><button className="v5-small-button" disabled={busy} onClick={() => void propose('trash')}><Trash2 size={14} />Move to Trash…</button>{labels?.some(label => label === 'TRASH' || label === 'SPAM') && <button className="v5-small-button" disabled={busy} onClick={() => void propose('restore')}><Undo2 size={14} />Restore…</button>}<button className="v5-small-button" onClick={() => navigate({ page: 'subscriptions', account })}>Review subscription options</button></div>{error && <p role="alert">{error}</p>}{preview && <ActionApproval key={preview.id} preview={preview} onChanged={onChanged} onClose={() => setPreview(null)} />}</details>
}
