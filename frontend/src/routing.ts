import { useEffect, useState, useSyncExternalStore } from 'react'
import type { AccountId, EmailMessage } from './api/emails'
export type Page = 'groups' | 'ai-activity' | 'sync-history' | 'spam' | 'categories' | 'overview' | 'inbox' | 'bills' | 'jobs' | 'courses' | 'promotions' | 'settings' | 'chat' | 'review' | 'subscriptions' | 'not-found' | 'cleanup-review' | 'trash' | 'automatic-cleanup' | `category:${string}`
export interface Route { page: Page; account: AccountId; messageId?: string; filter?:'eligible'|'protected'|'history' }
const documentId=crypto.randomUUID()
const paths: Record<string, Page> = {groups:'groups','ai-activity':'ai-activity', 'sync-history':'sync-history',spam:'spam', categories:'categories', 'cleanup-review':'cleanup-review',trash:'trash','automatic-cleanup':'automatic-cleanup', '': 'overview', overview: 'overview', inbox: 'inbox', bills: 'bills', jobs: 'jobs', courses: 'courses', promotions: 'promotions', settings: 'settings', chat: 'chat', review: 'review', subscriptions: 'subscriptions', orders: 'category:orders', security: 'category:security' }
export function parseRoute(url: URL): Route {
  const account = url.searchParams.get('account') ?? 'personal'
  let path: string
  try { path = decodeURIComponent(url.pathname.replace(/^\/|\/$/g, '')) } catch { return { page: 'not-found', account: 'personal' } }
  const page = paths[path] ?? (path.startsWith('categories/') && /^[a-z][a-z0-9_]{1,49}$/.test(path.slice(11)) ? `category:${path.slice(11)}` as Page : 'not-found')
  const messageId = url.searchParams.get('message') ?? undefined
  if (!['personal', 'university'].includes(account) || (messageId && !/^[a-zA-Z0-9_-]{1,256}$/.test(messageId))) return { page: 'not-found', account: 'personal' }
  return { page, account: account as AccountId, messageId, filter:['eligible','protected','history'].includes(url.searchParams.get('view')??'')?url.searchParams.get('view') as Route['filter']:undefined }
}
export function routeURL(route: Route) {
  const path = route.page.startsWith('category:') ? ['orders', 'security'].includes(route.page.slice(9)) ? route.page.slice(9) : `categories/${route.page.slice(9)}` : route.page === 'overview' ? '' : route.page
  const query = new URLSearchParams({ account: route.account })
  if(route.filter)query.set('view',route.filter)
  if (route.messageId) query.set('message', route.messageId)
  return `/${path}?${query}`
}
function subscribe(callback: () => void) {
  window.addEventListener('popstate', callback); window.addEventListener('postmark:navigate', callback)
  return () => { window.removeEventListener('popstate', callback); window.removeEventListener('postmark:navigate', callback) }
}
export function useRoute() {
  const location = useSyncExternalStore(subscribe, () => window.location.href)
  const route = parseRoute(new URL(location, window.location.origin))
  function navigate(next: Route, replace = false) {
    const current = parseRoute(new URL(window.location.href))
    if (current.page === next.page && current.account === next.account && current.messageId === next.messageId && current.filter === next.filter) return
    window.history[replace ? 'replaceState' : 'pushState']({ postmarkMessage: Boolean(next.messageId), documentId }, '', routeURL(next))
    window.dispatchEvent(new Event('postmark:navigate'))
  }
  function closeMessage() {
    if (!route.messageId) return
    if (window.history.state?.postmarkMessage && window.history.state?.documentId === documentId) window.history.back()
    else navigate({ page: route.page, account: route.account,filter:route.filter }, true)
  }
  return { route, navigate, closeMessage }
}
export function useRouteMessage(account: AccountId, id?: string) {
  const [result, setResult] = useState<{ key: string; message?: EmailMessage; error?: string } | null>(null)
  const key = `${account}:${id ?? ''}`
  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    fetch(`/api/local-messages/${account}/${encodeURIComponent(id)}`, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Message not found in this account, or it is unavailable.')
      return response.json()
    }).then(data => { if (!controller.signal.aborted) setResult({ key, message: { id, account_id: account, sender: data.sender, subject: data.subject, internal_date_ms: data.internal_date_ms } }) })
      .catch(err => { if (!controller.signal.aborted) setResult({ key, error: err instanceof Error ? err.message : 'Unable to open message' }) })
    return () => controller.abort()
  }, [account, id, key])
  return { selected: id && result?.key === key ? result.message ?? null : null, error: id && result?.key === key ? result.error : '', loading: Boolean(id && result?.key !== key) }
}
