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
  onSend: (draft: MailDraft) => void
  onUseDraft: () => void
}

export default function AssistantDraftCard({
  draft,
  delivery,
  reviewing,
  sending,
  onReview,
  onCancel,
  onSend,
  onUseDraft,
}: AssistantDraftCardProps) {
  const recipient = useRef<HTMLInputElement>(null)
  const status = delivery?.status
  const to = delivery && 'to' in delivery ? delivery.to : draft.to

  useEffect(() => {
    if (reviewing) recipient.current?.focus()
  }, [reviewing])

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const to = new FormData(event.currentTarget).get('to')
    if (typeof to === 'string' && to.trim()) onSend({ ...draft, to: to.trim() })
  }

  return (
    <div className="assistant-action">
      <strong>{draft.subject || '(No subject)'}</strong>
      {reviewing ? (
        <form className="assistant-send-review" onSubmit={send} aria-label="Review email before sending">
          <label>
            To
            <input ref={recipient} name="to" type="email" required defaultValue={draft.to} readOnly={sending} />
          </label>
          <p className="assistant-draft-body">{draft.body}</p>
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
      {status === 'sent' && <p role="status">Email sent.</p>}
      {status === 'compose' && <p role="status">Opened in Compose.</p>}
      {(delivery?.status === 'rejected' || delivery?.status === 'uncertain') && (
        <p className="error" role="alert">{delivery.message}</p>
      )}
    </div>
  )
}
