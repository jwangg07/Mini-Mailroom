import { useEffect, useRef } from 'react'
import type { FormEvent } from 'react'
import type { MailDraft } from './types'

export type DraftDelivery =
  | { status: 'sending' | 'compose' }
  | { status: 'sent'; to: string }
  | { status: 'rejected'; message: string }
  | { status: 'uncertain'; to: string; message: string }

type AssistantDraftCardProps = {
  draft: MailDraft
  delivery?: DraftDelivery
  reviewing: boolean
  sending: boolean
  onReview: () => void
  onCancel: () => void
  onEdit: (draft: MailDraft) => void
  onSend: () => void
  onUseDraft: () => void
}

export default function AssistantDraftCard({
  draft,
  delivery,
  reviewing,
  sending,
  onReview,
  onCancel,
  onEdit,
  onSend,
  onUseDraft,
}: AssistantDraftCardProps) {
  const reviewForm = useRef<HTMLFormElement>(null)
  const recipient = useRef<HTMLInputElement>(null)
  const status = delivery?.status
  const to = delivery && 'to' in delivery ? delivery.to : draft.to

  useEffect(() => {
    if (reviewing) {
      reviewForm.current?.scrollIntoView({ block: 'nearest' })
      recipient.current?.focus({ preventScroll: true })
    }
  }, [reviewing])

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSend()
  }

  return (
    <div className="assistant-action">
      {reviewing ? (
        <form ref={reviewForm} className="assistant-send-review" onSubmit={send} aria-label="Review email before sending">
          <label>
            To
            <input
              ref={recipient}
              type="email"
              required
              value={draft.to}
              onChange={(event) => onEdit({ ...draft, to: event.target.value })}
              readOnly={sending}
            />
          </label>
          <label>
            Subject
            <input
              value={draft.subject}
              maxLength={998}
              onChange={(event) => onEdit({ ...draft, subject: event.target.value })}
              readOnly={sending}
            />
          </label>
          <label>
            Message body
            <textarea
              value={draft.body}
              rows={5}
              required
              maxLength={100000}
              onChange={(event) => onEdit({ ...draft, body: event.target.value })}
              readOnly={sending}
            />
          </label>
          <div className="assistant-action-buttons">
            <button className="assistant-send" type="submit" disabled={sending}>
              {status === 'sending' ? 'Sending…' : 'Send email'}
            </button>
            <button className="assistant-chip" type="button" onClick={onCancel} disabled={sending}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <strong>{draft.subject || '(No subject)'}</strong>
          {to && <small>To {to}</small>}
          <details>
            <summary>Preview draft</summary>
            <p>{draft.body}</p>
          </details>
          {(status === undefined || status === 'rejected') && (
            <div className="assistant-action-buttons">
              <button className="assistant-chip" type="button" onClick={onReview} disabled={sending}>
                Review and send
              </button>
              <button className="assistant-chip" type="button" onClick={onUseDraft} disabled={sending}>
                Open in compose
              </button>
            </div>
          )}
        </>
      )}
      {status === 'sending' && !reviewing && <p role="status">Sending…</p>}
      {status === 'sent' && <p role="status">Email sent.</p>}
      {status === 'compose' && <p role="status">Opened in Compose.</p>}
      {(delivery?.status === 'rejected' || delivery?.status === 'uncertain') && (
        <p className="error" role="alert">{delivery.message}</p>
      )}
    </div>
  )
}
