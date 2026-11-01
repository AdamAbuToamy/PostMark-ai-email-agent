import { useEffect, useRef } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { X } from 'lucide-react'
import type { EmailMessage } from '../api/emails'
import { SemanticTimeline } from './SemanticTimeline'
import { EmailBody } from './EmailBody'
import { MessageActions } from './MessageActions'

export function MessageDetail({ message, onClose, onChanged, revision }: { message: EmailMessage; onClose: () => void; onChanged: () => void; revision: number }) {
  const dialog = useRef<HTMLElement>(null)
  const close = useRef<HTMLButtonElement>(null)
  const reduceMotion = useReducedMotion()
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    close.current?.focus()
    function trap(event: KeyboardEvent) {
      if (event.key !== 'Tab' || !dialog.current) return
      if (document.querySelector('.v5-palette')) return
      const controls = Array.from(dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary, [tabindex="0"]')).filter(element => {
        if (!element.getClientRects().length || getComputedStyle(element).visibility === 'hidden') return false
        // Chromium can report rectangles for controls in a closed details block;
        // those controls still cannot receive focus and must not be trap targets.
        for (let parent = element.parentElement; parent && parent !== dialog.current; parent = parent.parentElement) {
          if (parent instanceof HTMLDetailsElement && !parent.open && parent.querySelector(':scope > summary') !== element) return false
        }
        return true
      })
      const first = controls[0], last = controls[controls.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', trap)
    return () => {
      document.removeEventListener('keydown', trap)
      if (previous?.isConnected) previous.focus()
      else document.querySelector<HTMLElement>('main h1')?.focus()
    }
  }, [])
  return <motion.div className="v5-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose}>
    <motion.aside ref={dialog} className="v5-detail" initial={reduceMotion ? false : { x: '100%' }} animate={{ x: 0 }} exit={reduceMotion ? undefined : { x: '100%' }} transition={{ duration: reduceMotion ? 0 : 0.22 }} onClick={event => event.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="message-subject">
      <div className="v5-detail-header"><span>{message.account_id === 'personal' ? 'PERSONAL GMAIL' : 'BGU UNIVERSITY'}</span><button ref={close} onClick={onClose} aria-label="Close details"><X size={20} /></button></div>
      <h2 id="message-subject" dir="auto">{message.subject || '(No subject)'}</h2>
      <MessageActions key={`${message.account_id}:${message.id}`} account={message.account_id} messageId={message.id} onChanged={onChanged} revision={revision} />
      <SemanticTimeline key={`evidence:${message.account_id}:${message.id}`} account={message.account_id} id={message.id} />
      <EmailBody key={`${message.account_id}:${message.id}`} accountId={message.account_id} messageId={message.id} />
    </motion.aside>
  </motion.div>
}
