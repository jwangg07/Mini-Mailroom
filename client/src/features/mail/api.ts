import { requestJson } from '../../lib/http'
import type {
  AssistantExchange,
  AssistantReply,
  InboxPage,
  MailDraft,
  MailLabel,
  MailMessage,
} from './types'

const mailRequestFailed = 'Mail request failed. Please try again.'

export function getInbox(
  pageToken: string,
  signal: AbortSignal,
): Promise<InboxPage> {
  const query = new URLSearchParams({ page_token: pageToken })
  return requestJson(
    `/api/mail/messages?${query}`,
    { signal },
    mailRequestFailed,
  )
}

export function getMessage(
  id: string,
  signal: AbortSignal,
): Promise<MailMessage> {
  return requestJson(
    `/api/mail/messages/${encodeURIComponent(id)}`,
    { signal },
    mailRequestFailed,
  )
}

export function getLabels(signal: AbortSignal): Promise<MailLabel[]> {
  return requestJson('/api/mail/labels', { signal }, mailRequestFailed)
}

function postMail<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  return requestJson<T>(
    path,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Mailroom-Request': '1',
      },
      body: JSON.stringify(body),
      signal,
    },
    mailRequestFailed,
  )
}

export function getInlineImages(id: string, attachmentIds: string[], signal: AbortSignal) {
  return postMail<Record<string, string>>(
    `/api/mail/messages/${encodeURIComponent(id)}/inline-images`,
    { attachment_ids: attachmentIds },
    signal,
  )
}

export function changeLabel(id: string, labelId: string, applied: boolean) {
  return postMail<{ labelIds: string[] }>(
    `/api/mail/messages/${encodeURIComponent(id)}/labels`,
    { label_id: labelId, applied },
  )
}

export async function trashMessage(id: string): Promise<void> {
  await postMail(`/api/mail/messages/${encodeURIComponent(id)}/trash`)
}

export async function sendMail(draft: MailDraft): Promise<void> {
  // Never retry a send automatically: a lost response can still mean delivery.
  await postMail('/api/mail/send', draft)
}

export function askAssistant(
  request: {
    prompt: string
    history: AssistantExchange[]
    message_id?: string
    draft?: MailDraft | 'sent' | 'uncertain' | 'compose'
  },
  signal: AbortSignal,
): Promise<AssistantReply> {
  return postMail('/api/mail/assistant', request, signal)
}
