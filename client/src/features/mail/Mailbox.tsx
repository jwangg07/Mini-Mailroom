import { useEffect, useRef, useState } from 'react'
import { changeLabel, getLabels, getMessage, trashMessage } from './api'
import AssistantChat from './AssistantChat'
import ComposeForm from './ComposeForm'
import type { ComposeFormHandle } from './ComposeForm'
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
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [updating, setUpdating] = useState(false)
  const [error, setError] = useState('')
  const activeMessage = useRef<AbortController | null>(null)
  const updatePending = useRef(false)
  const inboxButton = useRef<HTMLButtonElement>(null)
  const composeButton = useRef<HTMLButtonElement>(null)
  const composeForm = useRef<ComposeFormHandle>(null)
  const assistantButton = useRef<HTMLButtonElement>(null)
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

  async function openMessage(id: string, reportError: (message: string) => void = setError) {
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
        reportError(
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

  async function updateMessage(
    action: 'trash' | { id: string; applied: boolean },
    reportError: (message: string) => void = setError,
  ) {
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
      reportError(
        reason instanceof Error ? reason.message : 'Could not update message.',
      )
    } finally {
      updatePending.current = false
      setUpdating(false)
    }
  }

  return (
    <section
      className={`mailbox${selected || openingId ? ' is-reading' : ''}${assistantOpen ? ' has-assistant' : ''}`}
      aria-label="Your mail"
    >
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
        <button
          className="assistant-toggle"
          ref={assistantButton}
          type="button"
          aria-expanded={assistantOpen}
          aria-controls="mail-assistant"
          onClick={() => setAssistantOpen((open) => !open)}
        >
          <span aria-hidden="true">✦</span> Assistant
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
      <AssistantChat
        open={assistantOpen}
        selected={selected}
        updating={updating}
        onClose={() => {
          setAssistantOpen(false)
          assistantButton.current?.focus()
        }}
        onUseDraft={(draft) => {
          if (!composeForm.current?.loadDraft(draft)) return false
          setAssistantOpen(false)
          setComposing(true)
          return true
        }}
        onApplyLabel={(messageId, labelId, reportError) => {
          if (selected?.id === messageId) {
            void updateMessage({ id: labelId, applied: true }, reportError)
          }
        }}
        onOpenMessage={openMessage}
      />
      <ComposeForm ref={composeForm} open={composing} onClose={() => {
        setComposing(false)
        composeButton.current?.focus()
      }} />
    </section>
  )
}
