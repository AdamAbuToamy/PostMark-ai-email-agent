import { useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import type { AccountId, EmailMessage } from '../api/emails'

interface SavedInsight {
  account_id: AccountId
  message_id: string
  model: string
  category: string
  priority: string
  summary: string
  action: string
}

interface Props {
  account: AccountId
  messages: EmailMessage[]
  onOpen: (message: EmailMessage) => void
}

function SavedInsightsContent({ account, messages, onOpen }: Props) {
  const [items, setItems] = useState<SavedInsight[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()

    fetch(`/api/insights/${account}`, {
      signal: controller.signal,
    })
      .then(async response => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }
        return response.json() as Promise<SavedInsight[]>
      })
      .then(data => {
        if (!controller.signal.aborted) {
          setItems(data)
        }
      })
      .catch(err => {
        if (!controller.signal.aborted) {
          setError(
            err instanceof Error ? err.message : 'Unable to load insights'
          )
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      })

    return () => controller.abort()
  }, [account])

  return (
    <section className="v5-saved-insights">
      <div className="v5-section-header">
        <div>
          <h2>
            <Sparkles size={18} />
            {' '}Saved AI Insights
          </h2>
          <span>Stored analysis results — no AI request needed</span>
        </div>
      </div>

      {loading && <p>Loading saved insights...</p>}

      {error && <p role="alert">Unable to load insights: {error}</p>}

      {!loading && !error && items.length === 0 && (
        <p>No saved AI insights for this account yet.</p>
      )}

      <div className="v5-saved-list">
        {items.map(item => {
          const original = messages.find(
            message =>
              message.account_id === item.account_id &&
              message.id === item.message_id
          )

          return (
            <article
              key={`${item.account_id}:${item.message_id}:${item.model}`}
              className="v5-saved-card"
            >
              <div className="v5-saved-tags">
                <span>{item.category}</span>
                <span>{item.priority} priority</span>
              </div>

              <p dir="auto">{item.summary || 'No summary available.'}</p>

              {item.action && (
                <p dir="auto">
                  <strong>Suggested action: </strong>
                  {item.action}
                </p>
              )}

              {original && (
                <button
                  type="button"
                  onClick={() => onOpen(original)}
                >
                  Open original email
                </button>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}

export function SavedInsights(props: Props) { return <SavedInsightsContent key={props.account} {...props} /> }
