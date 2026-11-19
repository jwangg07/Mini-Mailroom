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
  const [opening, setOpening] = useState(false)
  const [updating, setUpdating] = useState(false)
  const [error, setError] = useState('')
  const activeMessage = useRef<AbortController | null>(null)
  const updatePending = useRef(false)

  useEffect(() => () => activeMessage.current?.abort(), [])

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
    setOpening(true)
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
      if (!controller.signal.aborted) setOpening(false)
    }
  }

  async function updateMessage(action: 'trash' | { id: string; applied: boolean }) {
    if (!selected || updatePending.current) return
    updatePending.current = true
    setUpdating(true)
    setError('')
    try {
      if (action === 'trash') {
        await trashMessage(selected.id)
        setSelected(null)
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
    <section className="mailbox" aria-label="Your mail">
      <Inbox {...inbox} updating={updating} onOpenMessage={openMessage} />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {opening && <p role="status">Opening message...</p>}
      {selected && (
        <MessageDetail
          message={selected}
          labels={labels}
          labelsError={labelsError}
          updating={updating}
          onClose={() => setSelected(null)}
          onTrash={() => void updateMessage('trash')}
          onLabelChange={(id, applied) => void updateMessage({ id, applied })}
        />
      )}
      <ComposeForm />
    </section>
  )
}
