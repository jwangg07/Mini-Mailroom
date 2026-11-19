import { useEffect, useRef, useState } from 'react'
import { changeLabel, getLabels, getMessage, trashMessage } from './api'
import ComposeForm from './ComposeForm'
import Inbox from './Inbox'
import MessageDetail from './MessageDetail'
import type { MailLabel, MailMessage } from './types'
import { useInbox } from './useInbox'
import './mail.css'

export default function Mailbox() {
  const inbox = useInbox()
  const [selected, setSelected] = useState<MailMessage | null>(null)
  const [labels, setLabels] = useState<MailLabel[] | null>(null)
  const [labelsError, setLabelsError] = useState('')
  const [openingId, setOpeningId] = useState<string | null>(null)
  const [composing, setComposing] = useState(false)
  const [updating, setUpdating] = useState(false)
  const [error, setError] = useState('')
  const activeMessage = useRef<AbortController | null>(null)
  const updatePending = useRef(false)
  const inboxButton = useRef<HTMLButtonElement>(null)
  const composeButton = useRef<HTMLButtonElement>(null)
  const readingPane = useRef<HTMLElement>(null)

  useEffect(() => () => activeMessage.current?.abort(), [])

  const selectedId = selected?.id
  useEffect(() => {
    if (selectedId) readingPane.current?.focus({ preventScroll: true })
  }, [selectedId])

  const needsLabels = selected !== null && labels === null
  useEffect(() => {
    if (!needsLabels) return
    const controller = new AbortController()
    void getLabels(controller.signal).then(
      (result) => {
        if (!controller.signal.aborted) {
          setLabels(result)
          setLabelsError('')
        }
      },
      (reason) => {
        if (!controller.signal.aborted) {
          setLabelsError(
            reason instanceof Error ? reason.message : 'Could not load labels.',
          )
        }
      },
    )
    return () => controller.abort()
  }, [needsLabels])

  async function openMessage(id: string) {
    if (updatePending.current) return
    activeMessage.current?.abort()
    const controller = new AbortController()
    activeMessage.current = controller
    setOpeningId(id)
    setSelected(null)
    setError('')
    try {
      const message = await getMessage(id, controller.signal)
      if (!controller.signal.aborted) setSelected(message)
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(
          reason instanceof Error ? reason.message : 'Could not open message.',
        )
    } finally {
      if (!controller.signal.aborted) setOpeningId(null)
    }
  }

  function closeMessage() {
    activeMessage.current?.abort()
    setSelected(null)
    setOpeningId(null)
    setError('')
    requestAnimationFrame(() => inboxButton.current?.focus())
  }

  async function updateMessage(action: 'trash' | { id: string; applied: boolean }) {
    if (!selected || updatePending.current) return
    updatePending.current = true
    setUpdating(true)
    setError('')
    try {
      if (action === 'trash') {
        await trashMessage(selected.id)
        closeMessage()
        inbox.removeMessage(selected.id)
      } else {
        const { labelIds } = await changeLabel(
          selected.id,
          action.id,
          action.applied,
        )
        setSelected({ ...selected, labelIds })
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : 'Could not update message.',
      )
    } finally {
      updatePending.current = false
      setUpdating(false)
    }
  }

  return (
    <section className={selected || openingId ? 'mailbox is-reading' : 'mailbox'} aria-label="Your mail">
      <nav className="mailbox-nav" aria-label="Mailbox">
        <button
          className="compose-button"
          ref={composeButton}
          onClick={() => setComposing(true)}
          aria-expanded={composing}
          aria-controls="mail-compose"
        >
          <span aria-hidden="true">+</span> Compose
        </button>
        <button
          className="inbox-button"
          ref={inboxButton}
          disabled={updating}
          onClick={closeMessage}
        >
          Inbox
        </button>
        <a href="/auth/google/login?reconnect=true">Reconnect Gmail</a>
      </nav>
      <section className="inbox-pane" aria-label="Inbox messages">
        <Inbox
          {...inbox}
          activeId={selectedId ?? openingId ?? undefined}
          updating={updating}
          onOpenMessage={openMessage}
        />
      </section>
      <section className={error ? 'reading-pane has-error' : 'reading-pane'} aria-label="Reading pane" tabIndex={-1} ref={readingPane}>
        {error && <p className="error" role="alert">{error}</p>}
        {openingId && <p className="reading-status" role="status">Opening message…</p>}
        {selected ? (
          <MessageDetail
            key={selected.id}
            message={selected}
            labels={labels}
            labelsError={labelsError}
            updating={updating}
            onClose={closeMessage}
            onTrash={() => void updateMessage('trash')}
            onLabelChange={(id, applied) => void updateMessage({ id, applied })}
          />
        ) : !openingId && !error && (
          <p className="reading-status">Select an email to read it.</p>
        )}
      </section>
      <ComposeForm open={composing} onClose={() => {
        setComposing(false)
        composeButton.current?.focus()
      }} />
    </section>
  )
}
