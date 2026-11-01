import { useEffect, useState } from 'react'
import { Download, Image as ImageIcon, Paperclip } from 'lucide-react'
import { attachmentURL, type AccountId, type EmailAttachment } from '../api/emails'
function Attachment({ item, account, messageId }: { item: EmailAttachment; account: AccountId; messageId: string }) {
  const [preview, setPreview] = useState(false)
  const [image, setImage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    if (!preview) return
    const controller = new AbortController()
    let objectURL = ''
    fetch(attachmentURL(account, messageId, item.part_id, true), { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('This image is unavailable or unsafe to display.')
      const blob = await response.blob()
      if (blob.size > 10 * 1024 * 1024) throw new Error('Image exceeds the preview limit.')
      if (!controller.signal.aborted) { objectURL = URL.createObjectURL(blob); setImage(objectURL) }
    }).catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Image unavailable') })
    return () => { controller.abort(); if (objectURL) URL.revokeObjectURL(objectURL) }
  }, [preview, account, messageId, item.part_id])
  return <article className="v5-attachment"><div><Paperclip size={15} /><span><strong dir="auto">{item.filename}</strong><small>{item.mime_type} · {item.size === null ? 'Size not provided' : item.size ? `${Math.ceil(item.size / 1024)} KB` : 'Empty file'}</small></span>{item.available ? <a href={attachmentURL(account, messageId, item.part_id)} download aria-label={`Download ${item.filename}`}><Download size={16} /></a> : <small>Over size limit</small>}</div>
    {item.image && item.available && <button className="v5-small-button" onClick={() => { setPreview(value => !value); setImage(''); setError('') }}><ImageIcon size={13} />{preview ? 'Hide image' : 'Preview image'}</button>}
    {preview && (error ? <p role="alert">{error}</p> : image ? <img className="v5-attachment-image" src={image} alt={item.filename} /> : <p role="status">Loading image…</p>)}
  </article>
}
export function EmailAttachments({ items, account, messageId }: { items: EmailAttachment[]; account: AccountId; messageId: string }) {
  if (!items.length) return null
  return <section className="v5-attachments" aria-label="Attachments"><h3><Paperclip size={15} />Attachments <span>{items.length}</span></h3>{items.map(item => <Attachment key={item.part_id} item={item} account={account} messageId={messageId} />)}</section>
}
export function FormattedEmail({ html, account, messageId, allowExternal }: { html: string; account: AccountId; messageId: string; allowExternal: boolean }) {
  const [result, setResult] = useState<{ key: string; html: string } | null>(null)
  const key = `${account}:${messageId}:${allowExternal}:${html}`
  useEffect(() => {
    const controller = new AbortController()
    const document = new DOMParser().parseFromString(html, 'text/html')
    let remainingBytes = 20 * 1024 * 1024
    async function prepare() {
      for (const image of Array.from(document.querySelectorAll<HTMLImageElement>('img[data-attachment]')).slice(0, 10)) {
        try {
          const response = await fetch(attachmentURL(account, messageId, image.dataset.attachment ?? '', true), { signal: controller.signal })
          if (!response.ok) continue
          const blob = await response.blob()
          remainingBytes -= blob.size
          if (remainingBytes < 0 || controller.signal.aborted) break
          const data = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(blob) })
          image.src = data
        } catch { /* Keep the escaped alt text when an inline image is unavailable. */ }
      }
      if (allowExternal) for (const image of document.querySelectorAll<HTMLImageElement>('img[data-external-src]')) {
        const src = image.dataset.externalSrc ?? ''
        if (src.startsWith('https://')) { image.src = src; image.referrerPolicy = 'no-referrer' }
      }
      if (!controller.signal.aborted) setResult({ key, html: document.body.innerHTML })
    }
    void prepare()
    return () => controller.abort()
  }, [html, account, messageId, allowExternal, key])
  if (result?.key !== key) return <p role="status">Preparing safe formatted email…</p>
  const csp = "default-src 'none'; img-src data:" + (allowExternal ? ' https:' : '') + "; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
  const srcDoc = `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${csp}"><meta name="referrer" content="no-referrer"><style>body{font:14px/1.7 sans-serif;color:#252525;overflow-wrap:anywhere;margin:12px}img{max-width:100%;height:auto}table{max-width:100%}pre{white-space:pre-wrap}</style></head><body>${result.html}</body></html>`
  return <iframe className="v5-email-frame" title="Formatted email" tabIndex={-1} sandbox="" referrerPolicy="no-referrer" srcDoc={srcDoc} />
}
