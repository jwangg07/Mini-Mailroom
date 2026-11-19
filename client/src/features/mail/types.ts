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
