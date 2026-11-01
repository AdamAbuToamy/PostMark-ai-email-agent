import { motion, useReducedMotion } from 'motion/react'
import { Check, ChevronRight, Undo2 } from 'lucide-react'
import type { EmailMessage } from '../api/emails'
import {isModelAnalysis} from '../processingStates'

interface Props {
  message: EmailMessage
  onOpen: (message: EmailMessage) => void
  localState?: 'new' | 'done'
  needsReview?: boolean
  notice?: string | null
  semantic?: {status?: string|null;entity?:string|null;source?:string;factuality?:string;input_coverage?:{body_supplied:boolean;excerpt_characters:number;body_complete?:boolean};status_source?:string;status_note?:string;message_type?:string;primary_intent?:string;understood?:boolean}
  jobStatus?: {state: string; label: string} | null
  onToggle?: () => void
  busy?: boolean
}
export function MailboxMessageRow({ message, onOpen, localState, needsReview, notice, jobStatus, semantic, onToggle, busy }: Props) {
  const reduceMotion = useReducedMotion()
  const rowNotice = notice ?? message.gmail_location
  const rawLabel=semantic&&isModelAnalysis(semantic.source)&&semantic.understood!==false?(semantic.message_type&&!['notification','message','update','other','unknown'].includes(semantic.message_type.toLowerCase())?semantic.message_type:semantic.primary_intent):''
  const typeLabel=rawLabel?rawLabel.replaceAll('_',' ').replace(/^./,letter=>letter.toUpperCase()):''
  const sender = message.sender.split('<')[0].replace(/["']/g, '').trim() || message.sender
  const date = message.internal_date_ms ? new Date(message.internal_date_ms) : null
  const validDate = date && !Number.isNaN(date.getTime()) ? date : null
  return <motion.div className={`v5-mail-row ${localState === 'done' ? 'is-done' : ''}`} initial={reduceMotion ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: reduceMotion ? 0 : 0.16 }}>
    <button className="v5-message" onClick={() => onOpen(message)} aria-label={`Open ${message.subject || 'message without subject'} from ${sender}`}>
      <span className="v5-message-avatar" aria-hidden="true">{sender.slice(0, 1).toUpperCase()}</span>
      <span className="v5-message-data"><strong dir="auto">{sender}</strong><span dir="auto">{message.subject || '(No subject)'}</span></span>
      <span className="v5-row-meta">
        {validDate && <time dateTime={validDate.toISOString()} title={validDate.toLocaleString()}>{validDate.toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...(validDate.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' as const } : {}) })}</time>}
        <span className="v5-row-badges">{semantic?.status?<span className="v5-status-badge pm-job-status" title={semantic.status_source==='local'?'Local message evidence':isModelAnalysis(semantic.source)?'AI interpretation; current outcome unverified':'Local message evidence'}>{semantic.status.replaceAll('_',' ')}</span>:jobStatus && <span className="v5-status-badge pm-job-status" data-state={jobStatus.state}>{jobStatus.label}</span>}{!semantic?.status&&!jobStatus&&typeLabel&&<span className="v5-status-badge" title="AI interpretation; inspect message evidence">{typeLabel}</span>}{semantic?.entity&&<span className="v5-status-badge" title={isModelAnalysis(semantic.source)?semantic.input_coverage?.body_complete?'AI received complete message text; historical context separated':semantic.input_coverage?.body_supplied?`AI interpretation from a ${semantic.input_coverage.excerpt_characters}-character body excerpt; full body not analyzed`:'AI interpretation; body input unavailable or subject only':'Locally extracted entity'}>{semantic.entity}</span>}{localState === 'done' ? <span className="v5-status-badge done"><Check size={11} />Done</span> : needsReview ? <span className="v5-status-badge review">Review needed</span> : null}{rowNotice && <span className="v5-status-badge">{rowNotice}</span>}</span>
      </span>
      <ChevronRight size={16} className="v5-row-chevron" aria-hidden="true" />
    </button>
    {onToggle && <button className="v5-row-action" onClick={onToggle} disabled={busy} title={localState === 'done' ? 'Return to New' : 'Mark Done'} aria-label={`${localState === 'done' ? 'Return to New' : 'Mark Done'}: ${message.subject}`}>
      {localState === 'done' ? <Undo2 size={17} /> : <Check size={17} />}
    </button>}
  </motion.div>
}
