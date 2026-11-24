export type MailSummary = {
  id: string
  from: string
  subject: string
  date: string
  snippet: string
  unread: boolean
}

export type MailMessage = {
  id: string
  from: string
  subject: string
  body: string
  html: string | null
  labelIds: string[]
  inlineImages: Record<string, InlineImage>
}

type InlineImage = {
  mimeType: string
  data?: string
  attachmentId?: string
}

export type MailLabel = {
  id: string
  name: string
}

export type InboxPage = {
  messages: MailSummary[]
  nextPageToken: string | null
}

export type MailDraft = {
  to: string
  subject: string
  body: string
}

export type AssistantExchange = {
  prompt: string
  answer: string
}

export type AssistantAction =
  | { kind: 'draft'; to?: string; subject: string; body: string }
  | { kind: 'label'; message_id: string; label_id: string; label_name: string }

export type AssistantReply = {
  answer: string
  actions?: AssistantAction[]
}
