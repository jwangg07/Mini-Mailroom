import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { sendMail } from './api'
import type { MailDraft } from './types'

export default function ComposeForm({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [draft, setDraft] = useState<MailDraft>({
    to: '',
    subject: '',
    body: '',
  })
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  // A ref blocks a second submit before React renders the disabled fieldset.
  const sendingRequest = useRef(false)
  const recipient = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (open) recipient.current?.focus()
  }, [open])

  async function sendDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (sendingRequest.current) return
    sendingRequest.current = true
    setSending(true)
    setError('')
    setNotice('')
    try {
      await sendMail(draft)
      setDraft({ to: '', subject: '', body: '' })
      setNotice('Message sent.')
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Send result unknown. Check Sent in Gmail before sending again.',
      )
    } finally {
      sendingRequest.current = false
      setSending(false)
    }
  }

  return (
    <form id="mail-compose" className="compose" hidden={!open} onSubmit={(event) => void sendDraft(event)}>
      <div className="compose-heading">
        <h2>New message</h2>
        <button className="secondary" type="button" onClick={onClose}>Close</button>
      </div>
      <fieldset disabled={sending}>
        <label htmlFor="recipient">To</label>
        <input
          id="recipient"
          ref={recipient}
          type="email"
          required
          value={draft.to}
          onChange={(event) => setDraft({ ...draft, to: event.target.value })}
        />
        <label htmlFor="subject">Subject</label>
        <input
          id="subject"
          maxLength={998}
          value={draft.subject}
          onChange={(event) =>
            setDraft({ ...draft, subject: event.target.value })
          }
        />
        <label htmlFor="body">Message</label>
        <textarea
          id="body"
          required
          rows={8}
          maxLength={100000}
          value={draft.body}
          onChange={(event) => setDraft({ ...draft, body: event.target.value })}
        />
        <button className="google-button" type="submit">
          {sending ? 'Sending...' : 'Send email'}
        </button>
      </fieldset>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
    </form>
  )
}
