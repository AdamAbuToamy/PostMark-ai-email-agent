import { useEffect, useState } from 'react'
import { RefreshCw, AlertCircle, CalendarDays } from 'lucide-react'
import { EmailAttachments, FormattedEmail } from './EmailAttachments'

import {
  getEmailContent,
  type AccountId,
  type EmailContent,
} from '../api/emails'

interface Props {
  accountId: AccountId
  messageId: string
}

export function EmailBody({ accountId, messageId }: Props) {
  const [result, setResult] = useState<{ key: string; content?: EmailContent; error?: string } | null>(null)
  const [retry, setRetry] = useState(0)
  const [formatted, setFormatted] = useState(false)
  const [allowExternal, setAllowExternal] = useState(false)
  const key = `${accountId}:${messageId}:${retry}`
  const loading = result?.key !== key
  const error = result?.key === key ? result.error : ''
  const content = result?.key === key ? result.content : null

  useEffect(() => {
    const controller = new AbortController()

    getEmailContent(accountId, messageId, controller.signal)
      .then(content => {
        if (!controller.signal.aborted) setResult({ key, content })
      })
      .catch(err => {
        if (!controller.signal.aborted) {
          setResult({ key, error: err instanceof Error ? err.message : 'Unable to read message' })
        }
      })


    return () => controller.abort()
  }, [accountId, messageId, key])

  if (loading) {
    return (
      <div className="email-body-state" role="status">
        <RefreshCw className="v5-spinning" size={20} />
        <span>Loading message...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="email-body-state" role="alert">
        <AlertCircle size={22} />
        <span>{error}</span>
        <button onClick={() => setRetry(n => n + 1)}>
          Try again
        </button>
      </div>
    )
  }

  if (!content) return null

  return (
    <div className="email-body-content">
      <div className="email-body-headers">
        <p><strong>From:</strong> {content.sender || 'Unknown'}</p>
        <p><strong>To:</strong> {content.recipient || 'Not specified'}</p>
        {content.cc && (
          <p><strong>CC:</strong> {content.cc}</p>
        )}
      </div>

      {content.date && (
        <div className="email-body-date">
          <CalendarDays size={15} />
          <span>{content.date}</span>
        </div>
      )}

      <div className="email-body-divider" />

      {content.html && <div className="v5-email-format"><button className="v5-small-button" onClick={() => setFormatted(value => !value)}>{formatted ? 'Plain text' : 'Formatted email & inline images'}</button>{Boolean(content.external_images) && <button className="v5-small-button" disabled={allowExternal} onClick={() => { if (window.confirm(`Load external images for this message from ${(content.external_image_hosts ?? []).slice(0, 10).join(', ') || 'external hosts'}? Their hosts can learn your IP and that you opened this email.`)) setAllowExternal(true) }}>{allowExternal ? 'External images allowed' : `Load external images (${content.external_images})`}</button>}</div>}
      {formatted && content.html ? <FormattedEmail html={content.html} account={accountId} messageId={messageId} allowExternal={allowExternal} /> : <div className="email-body-text">{content.body || 'This message has no readable text content.'}</div>}
      <EmailAttachments items={content.attachments ?? []} account={accountId} messageId={messageId} />
    </div>
  )
}
