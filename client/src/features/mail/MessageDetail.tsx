import EmailBody from './EmailBody'
import type { MailLabel, MailMessage } from './types'

type MessageDetailProps = {
  message: MailMessage
  labels: MailLabel[] | null
  labelsError: string
  updating: boolean
  onClose: () => void
  onTrash: () => void
  onLabelChange: (id: string, applied: boolean) => void
}

export default function MessageDetail({
  message,
  labels,
  labelsError,
  updating,
  onClose,
  onTrash,
  onLabelChange,
}: MessageDetailProps) {
  const appliedLabels = labels
    ?.filter((label) => message.labelIds.includes(label.id))
    .map((label) => label.name)
  return (
    <article className="message-detail" aria-label="Open message">
      <div className="mail-toolbar">
        <button className="secondary" disabled={updating} onClick={onClose}>
          Back to inbox
        </button>
        <button className="secondary" disabled={updating} onClick={onTrash}>
          Move to Trash
        </button>
      </div>
      <h2>{message.subject || '(No subject)'}</h2>
      <p className="message-sender">{message.from}</p>
      <details>
        <summary>
          {appliedLabels?.length ? `Labels (${appliedLabels.join(', ')})` : 'Labels'}
        </summary>
        {labelsError && (
          <p className="error" role="alert">{labelsError}</p>
        )}
        {labels === null && !labelsError && <p role="status">Loading labels...</p>}
        {labels?.length === 0 && <p>No Gmail labels.</p>}
        {labels && labels.length > 0 && (
          <fieldset aria-label="Gmail labels" disabled={updating}>
            {labels.map((label) => (
              <label key={label.id}>
                <input
                  type="checkbox"
                  checked={message.labelIds.includes(label.id)}
                  onChange={(event) => onLabelChange(label.id, event.target.checked)}
                />
                {label.name}
              </label>
            ))}
          </fieldset>
        )}
      </details>
      <EmailBody message={message} />
    </article>
  )
}
