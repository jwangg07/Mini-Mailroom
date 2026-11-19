import type { InboxPage } from './types'

type InboxProps = {
  page: InboxPage
  pageToken: string
  loading: boolean
  updating: boolean
  activeId?: string
  error: string
  refreshInbox: () => void
  showOlderMessages: () => void
  onOpenMessage: (id: string) => Promise<void>
}

const shortDate = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

export default function Inbox({
  page,
  pageToken,
  loading,
  updating,
  activeId,
  error,
  refreshInbox,
  showOlderMessages,
  onOpenMessage,
}: InboxProps) {
  return (
    <>
      <div className="mail-toolbar inbox-toolbar">
        <h2>Inbox</h2>
        <button
          className="secondary"
          disabled={loading || updating}
          onClick={refreshInbox}
        >
          Refresh
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && (
        <p className="inbox-status" role="status">Loading your inbox…</p>
      )}
      {!loading && !error && page.messages.length === 0 && (
        <p className="inbox-status">Your inbox is empty.</p>
      )}
      {page.messages.length > 0 && (
        <ul className="message-list">
          {page.messages.map((mail) => {
            const sentAt = Date.parse(mail.date)
            return (
              <li key={mail.id}>
                <button
                  className={mail.unread ? 'message-row unread' : 'message-row'}
                  disabled={updating}
                  aria-current={mail.id === activeId ? 'true' : undefined}
                  onClick={() => void onOpenMessage(mail.id)}
                >
                  <span className="message-from">
                    {mail.unread && <span className="sr-only">Unread: </span>}
                    {mail.from}
                  </span>
                  <span className="message-subject">
                    {mail.subject || '(No subject)'}
                  </span>
                  <span className="message-snippet">{mail.snippet}</span>
                  <span className="message-date" title={mail.date}>
                    {Number.isNaN(sentAt) ? '' : shortDate.format(sentAt)}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {(page.nextPageToken || pageToken) && (
        <div className="mail-toolbar inbox-pagination">
          {page.nextPageToken && (
            <button
              className="secondary"
              disabled={loading || updating}
              onClick={showOlderMessages}
            >
              Older messages
            </button>
          )}
          {pageToken && (
            <button
              className="secondary"
              disabled={loading || updating}
              onClick={refreshInbox}
            >
              Newest messages
            </button>
          )}
        </div>
      )}
    </>
  )
}
