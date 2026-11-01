import { useState } from 'react'
import { Sparkles, RefreshCw } from 'lucide-react'
import type { AccountId, EmailMessage } from '../api/emails'

interface Insight {
  message_id: string
  account_id: AccountId
  subject: string
  category: string
  priority: string
  summary: string
  action: string
}

interface AnalysisResponse {
  items: Insight[]
  cached: number
  analyzed: number
}

interface Props {
  account: AccountId
  messages: EmailMessage[]
  onOpen: (message: EmailMessage) => void
  onAnalyzed?: () => void
}

function AIInsightsContent({ account, messages, onOpen, onAnalyzed }: Props) {
  const [result, setResult] = useState<AnalysisResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')


  async function analyze() {
    if (loading) return

    const approved = window.confirm(
      `Analyze up to 3 recent emails from ${account}?\n\n` +
      'The sender, subject and up to 3000 characters of each ' +
      'uncached email will be sent to Google Gemini.\n\n' +
      'This may expose private information and may incur API charges. ' +
      'Results are cached locally. No Gmail messages will be changed.'
    )

    if (!approved) return

    setLoading(true)
    setError('')

    try {
      const response = await fetch(`/api/analyze/${account}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved: true }),
      })

      if (!response.ok) {
        const details = await response.json().catch(() => null)
        throw new Error(
          typeof details?.detail === 'string'
            ? details.detail
            : `Analysis failed: HTTP ${response.status}`
        )
      }

      setResult(await response.json() as AnalysisResponse)
      onAnalyzed?.()
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Analysis failed'
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="v5-ai-insights">
      <div className="v5-ai-heading">
        <div>
          <h2><Sparkles size={20} /> AI Inbox Analysis</h2>
          <p>Find important emails and suggested next steps.</p>
        </div>

        <button onClick={analyze} disabled={loading}>
          {loading
            ? <RefreshCw size={16} className="v5-spinning" />
            : <Sparkles size={16} />}
          {loading ? 'Analyzing...' : 'Analyze Inbox'}
        </button>
      </div>

      {error && <p className="v5-ai-error" role="alert">{error}</p>}

      {result && (
        <div className="v5-ai-results">
          <p className="v5-ai-meta">
            {result.analyzed} analyzed with Gemini ·
            {' '}{result.cached} loaded from cache
          </p>

          {result.items.map(item => {
            const message = messages.find(
              m => m.id === item.message_id &&
                   m.account_id === item.account_id
            )

            return (
              <article
                className="v5-ai-card"
                key={`${item.account_id}:${item.message_id}`}
              >
                <div className="v5-ai-tags">
                  <span>{item.category}</span>
                  <span>{item.priority} priority</span>
                </div>

                <h3>{item.subject}</h3>
                <p dir="auto">{item.summary}</p>

                {item.action && (
                  <p className="v5-ai-action" dir="auto">
                    <strong>Action:</strong> {item.action}
                  </p>
                )}

                {message && (
                  <button
                    className="v5-ai-open"
                    onClick={() => onOpen(message)}
                  >
                    Open original email
                  </button>
                )}
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}

export function AIInsights(props: Props) { return <AIInsightsContent key={props.account} {...props} /> }
