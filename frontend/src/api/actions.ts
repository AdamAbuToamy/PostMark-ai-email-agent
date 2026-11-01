import { chatSessionHeaders } from './chatSession'
import type { AccountId } from './emails'
import { readLocal } from './mailboxes'
export interface PreviewItem { label_ids?:string[]|null; manual_destinations?:string[]; subscription_id?: string; display_name?:string; identity?: string; eligible?: boolean; requires_third_party_ack?:boolean; host?: string; reason?: string; proof?: { url: string; signer: string; author_domain?:string; alignment?:string; author_authorization?:string }; message_id: string; sender?: string; subject?: string; category_id?: string; classification?: { category_id?: string } }
export interface ActionPreview {
  id: string; account_id: AccountId; kind: string; status: string; expires: number
  payload: { keep_after_restore?:boolean; description?: string | null; operation?: string; category_id?: string; bulk?: boolean; args?: Record<string, unknown>; items: PreviewItem[] }
  result?: { message?: string; succeeded?: number; items?: { message_id: string; status: string; message?: string }[] } | null
}
export interface Capabilities { account_id: AccountId; gmail_modify: boolean; scope: string; upgrade_command: string }
export class LocalActionError extends Error {
  readonly status:number
  constructor(message:string,status:number){super(message);this.name='LocalActionError';this.status=status}
}
export async function postLocal<T>(url: string, body: object): Promise<T> {
  const response = await fetch(url, { method: 'POST', headers: { ...chatSessionHeaders(), 'Content-Type': 'application/json', 'X-Email-Agent-Action': 'message-management' }, body: JSON.stringify(body) })
  if (!response.ok) { const error = await response.json().catch(() => null); const fields=Array.isArray(error?.detail)?error.detail.map((e:{loc?:unknown[]})=>e.loc?.filter(v=>typeof v==='string').join('.')).join(', '):''; throw new LocalActionError(typeof error?.detail === 'string' ? error.detail : fields?`Invalid request fields: ${fields}`:`Postmark request failed (HTTP ${response.status})`,response.status) }
  try{return await response.json() as T}catch{throw new LocalActionError('Postmark returned an unreadable response. No automatic retry was made.',response.status)}
}
export const actionApi = {
  preview: (account: AccountId, operation: 'trash' | 'restore', message_ids: string[], category_id?: string, individual_selection = false) => postLocal<ActionPreview>(`/api/gmail/${account}/preview`, { operation, message_ids, category_id, individual_selection }),
  capabilities: (account: AccountId) => readLocal<Capabilities>(`/api/gmail/${account}/capabilities`),
  confirm: (preview: ActionPreview, approved_targets?: string[], approved_third_party_targets?:string[]) => postLocal<ActionPreview>(preview.kind.startsWith('unsubscribe:') ? `/api/subscriptions/${preview.account_id}/actions/${preview.id}/confirm` : preview.kind.startsWith('gmail:') ? `/api/gmail/${preview.account_id}/actions/${preview.id}/confirm` : `/api/chat/${preview.account_id}/actions/${preview.id}/confirm`, { approved: true, ...(preview.kind.startsWith('unsubscribe:') ? { approved_targets, approved_third_party_targets:approved_third_party_targets??[] } : {}) }),
}
