import { chatSessionHeaders } from './chatSession'
import type { AccountId } from './emails'

export interface Counts { new: number; done: number; all: number }
export interface Category { id: string; name: string; icon_key: string; source: string; counts?: Counts }
export interface MailboxMessage {
  processing?:{status:string;reason:string;provider?:string;model?:string}
  account_id: AccountId
  message_id: string
  category_id: string
  sender: string
  subject: string
  internal_date_ms: number
  local_state: 'new' | 'done'
  attention_state?: string
  section_id?: string
  semantic?: {status?: string|null; entity?: string|null; source?:string; factuality?:string; primary_intent?:string; message_type?:string; input_coverage?:{body_supplied:boolean;excerpt_characters:number;body_complete:boolean};status_source?:string;status_note?:string;understood?:boolean}
  job_status?: {state: string; label: string} | null
  classification_status: string
  order_notice: string | null
  gmail_location?: string | null
}
export interface MailboxResult { processing?:{scope:string;total:number;counts:Record<string,number>;moved_24h:number;last_activity:string|null}; discovery?:{source:string;truncated:boolean;analyzed:number}; sections?:{id:string;name:string;count:number;reason?:string}[]; items: MailboxMessage[]; counts: Counts; total: number; next_offset: number | null }
export interface MessageDecision {
  category_id?: string
  local_state: 'new' | 'done'
  source?: string
  status?: string
  reason?: string
  rule_version?: string
  label_ids?: string[] | null
}

export async function readLocal<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, headers:chatSessionHeaders() })
  if (!response.ok) {
    const details = await response.json().catch(() => null)
    throw new Error(typeof details?.detail === 'string' ? details.detail : `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}
const messagePath = (account: AccountId, id: string) => `/api/local-messages/${account}/${encodeURIComponent(id)}`
async function mutate<T>(url: string, payload: object): Promise<T> {
  const response = await fetch(url, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-Email-Agent-Action': 'message-management' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const details = await response.json().catch(() => null)
    throw new Error(typeof details?.detail === 'string' ? details.detail : `Unable to save (${response.status})`)
  }
  return response.json() as Promise<T>
}
export const mailboxApi = {
  categories: (account: AccountId, signal?: AbortSignal) => readLocal<Category[]>(`/api/category-mailbox/${account}/categories`, signal),
  details: (account: AccountId, id: string, signal?: AbortSignal) => readLocal<MessageDecision>(messagePath(account, id), signal),
  status: (account: AccountId, id: string, state: 'new' | 'done') => mutate<{ local_state: 'new' | 'done' }>(`${messagePath(account, id)}/status`, { state }),
  move: (account: AccountId, id: string, category_id: string, application_ref: string) => mutate<MessageDecision>(`${messagePath(account, id)}/category`, { category_id, application_ref }),
}

// Status DTOs contain no saved secrets. Share concurrent reads, never account state.
const engineSnapshots = new Map<string, {value: unknown; at: number}>()
const engineReads = new Map<string, Promise<unknown>>()
export function lastEngineStatus<T>(account: AccountId): T | null {
  return (engineSnapshots.get(account)?.value as T | undefined) ?? null
}
export function readEngineStatus<T>(account: AccountId, force = false): Promise<T> {
  const cached = engineSnapshots.get(account)
  if (!force && cached && Date.now() - cached.at < 2000) return Promise.resolve(cached.value as T)
  const pending = engineReads.get(account)
  if (!force && pending) return pending as Promise<T>
  const request = readLocal<T>(`/api/hybrid/${account}`, AbortSignal.timeout(10000)).then(value => {
    engineSnapshots.set(account, {value, at: Date.now()})
    return value
  }).finally(() => { if (engineReads.get(account) === request) engineReads.delete(account) })
  engineReads.set(account, request)
  return request
}
