export type AccountId = 'personal' | 'university'

export interface EmailAccount {
  id: AccountId
  message_count: number
}

export interface EmailMessage {
  id: string
  account_id: AccountId
  sender: string
  subject: string
  internal_date_ms?: number
  gmail_location?: string | null
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal })

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`)
  }

  return response.json() as Promise<T>
}

export interface SyncResult {
  account_id: AccountId
  fetched: number
  stored: number
}

export const emailApi = {
  sync: async (account: AccountId): Promise<SyncResult> => {
    const response = await fetch(`/api/sync/${account}`, {
      method: 'POST',
    })
    if (!response.ok) {
      throw new Error(`Sync failed: HTTP ${response.status}`)
    }
    return response.json() as Promise<SyncResult>
  },

  accounts: (signal?: AbortSignal) =>
    request<EmailAccount[]>('/api/accounts', signal),

  messages: (account: AccountId, signal?: AbortSignal) =>
    request<EmailMessage[]>(
      `/api/messages?account_id=${encodeURIComponent(account)}&limit=100`,
      signal,
    ),
}

export interface EmailAttachment { part_id: string; filename: string; mime_type: string; size: number | null; inline: boolean; image: boolean; available: boolean }
export interface EmailContent {
  id: string
  sender: string
  recipient: string
  cc: string
  subject: string
  date: string
  body: string
  html?: string
  external_images?: number
  external_image_hosts?: string[]
  attachments?: EmailAttachment[]
}

export async function getEmailContent(
  account: AccountId,
  messageId: string,
  signal?: AbortSignal,
): Promise<EmailContent> {
  const url = `/api/messages/${encodeURIComponent(account)}/${encodeURIComponent(messageId)}/content`

  const response = await fetch(url, { signal })

  if (!response.ok) {
    throw new Error(`Unable to load message (HTTP ${response.status})`)
  }

  return response.json() as Promise<EmailContent>
}

export function attachmentURL(account: AccountId, message: string, part: string, inline = false) {
  return `/api/messages/${account}/${encodeURIComponent(message)}/attachments/${encodeURIComponent(part)}${inline ? '?inline=true' : ''}`
}
