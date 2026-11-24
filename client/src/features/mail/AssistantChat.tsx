import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import Markdown from 'react-markdown'
import type { Components } from 'react-markdown'
import { HttpError } from '../../lib/http'
import { askAssistant, sendMail } from './api'
import AssistantDraftCard from './AssistantDraftCard'
import type { DraftDelivery } from './AssistantDraftCard'
import type { AssistantReply, MailDraft, MailMessage, MessageLabelUpdate } from './types'
import './assistant.css'

type Question = { prompt: string; context: string }
type Exchange = Question & { reply: AssistantReply }
type ReportError = (message: string) => void

type AssistantChatProps = {
  open: boolean
  selected: MailMessage | null
  updating: boolean
  onClose: () => void
  onUseDraft: (draft: MailDraft) => boolean
  onChangeLabel: (messageId: string, labelId: string, applied: boolean, reportError: ReportError) => Promise<boolean>
  onLabelsUpdated: (updates: MessageLabelUpdate[]) => void
  onOpenMessage: (messageId: string, reportError: ReportError) => Promise<void>
}

const inboxPrompts = [
  { label: 'Catch me up', text: 'Summarize my recent inbox and flag anything that needs a reply.' },
  { label: 'Find unread mail', text: 'Find my unread inbox emails and tell me who they’re from.' },
  { label: 'Label recent mail', text: 'Read my latest five emails and apply relevant labels.' },
  { label: 'Draft email', text: 'Draft an email to ' },
]
const messagePrompts = [
  { label: 'Summarize', text: 'Summarize this email, including any requests or deadlines.' },
  { label: 'Draft reply', text: 'Draft a concise reply to this email.' },
  { label: 'Suggest labels', text: 'Which of my existing Gmail labels would fit this email?' },
  { label: 'Label email', text: 'Apply label ' },
]

const uncertainDelivery = 'Send result unknown. Check Sent in Gmail before sending again.'
const markdownComponents: Components = {
  a: ({ children, href }) => href
    ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
    : <span>{children}</span>,
  // Assistant text may include image URLs copied from an email.
  img: ({ alt }) => alt ? <span>{alt}</span> : null,
}

function latestDraftId(turns: Exchange[]): string | undefined {
  const turnIndex = turns.findLastIndex(({ reply }) => reply.actions?.some(({ kind }) => kind === 'draft'))
  if (turnIndex >= 0) {
    const actionIndex = turns[turnIndex].reply.actions?.findLastIndex(({ kind }) => kind === 'draft')
    return `${turnIndex}:${actionIndex}`
  }
}

export default function AssistantChat({
  open,
  selected,
  updating,
  onClose,
  onUseDraft,
  onChangeLabel,
  onLabelsUpdated,
  onOpenMessage,
}: AssistantChatProps) {
  const [includeMessage, setIncludeMessage] = useState(true)
  const [input, setInput] = useState('')
  const [turns, setTurns] = useState<Exchange[]>([])
  const [pending, setPending] = useState<Question | null>(null)
  const [error, setError] = useState('')
  const [reviewId, setReviewId] = useState<string | null>(null)
  const [editedDrafts, setEditedDrafts] = useState<Record<string, MailDraft>>({})
  const [deliveries, setDeliveries] = useState<Record<string, DraftDelivery>>({})
  const currentDeliveries = useRef(deliveries)
  const writingMail = useRef(false)
  const request = useRef<AbortController | null>(null)
  const promptField = useRef<HTMLTextAreaElement>(null)
  const transcript = useRef<HTMLDivElement>(null)
  const message = includeMessage ? selected : null
  const context = message ? message.subject || '(No subject)' : 'Inbox'
  const prompts = message ? messagePrompts : inboxPrompts
  const busy = pending !== null || Object.values(deliveries).some(({ status }) => status === 'sending')

  useEffect(() => () => request.current?.abort(), [])

  useEffect(() => {
    if (open) promptField.current?.focus()
  }, [open])

  useEffect(() => {
    const log = transcript.current
    if (open && log) log.scrollTop = log.scrollHeight
  }, [open, turns, pending, error])

  function reset() {
    if (writingMail.current || request.current) return
    setTurns([])
    setInput('')
    setError('')
    setReviewId(null)
    setEditedDrafts({})
    currentDeliveries.current = {}
    setDeliveries(currentDeliveries.current)
    promptField.current?.focus()
  }

  function setDelivery(id: string, delivery: DraftDelivery) {
    currentDeliveries.current = { ...currentDeliveries.current, [id]: delivery }
    setDeliveries(currentDeliveries.current)
  }

  function getDraft(id: string): MailDraft | undefined {
    if (editedDrafts[id]) return editedDrafts[id]
    const [turnIndex, actionIndex] = id.split(':').map(Number)
    const action = turns[turnIndex]?.reply.actions?.[actionIndex]
    if (action?.kind === 'draft') {
      return { to: action.to ?? '', subject: action.subject, body: action.body }
    }
  }

  async function sendDraft(id: string, current: MailDraft) {
    if (writingMail.current || request.current) return
    const delivery = currentDeliveries.current[id]
    if (delivery && delivery.status !== 'rejected') return
    const draft = { ...current, to: current.to.trim() }
    if (!draft.to || !draft.body.trim()) {
      setReviewId(id)
      setDelivery(id, { status: 'rejected', message: 'Enter a recipient and a message body.' })
      return
    }
    writingMail.current = true
    setDelivery(id, { status: 'sending' })
    try {
      await sendMail(draft)
      setDelivery(id, { status: 'sent', to: draft.to })
      setReviewId(null)
    } catch (reason) {
      if (reason instanceof HttpError && reason.status >= 400 && reason.status < 500 && reason.status !== 408) {
        setReviewId(id)
        setDelivery(id, {
          status: 'rejected',
          message: reason.status === 422 ? 'Check the recipient address, subject, and message body.' : reason.message,
        })
      } else {
        setDelivery(id, { status: 'uncertain', to: draft.to, message: uncertainDelivery })
        setReviewId(null)
      }
    } finally {
      writingMail.current = false
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const prompt = input.trim()
    if (request.current || writingMail.current || !prompt) return
    const question = { prompt, context }
    setInput('')
    setError('')
    const controller = new AbortController()
    request.current = controller
    const draftId = reviewId ?? latestDraftId(turns)
    const draftStatus = draftId ? currentDeliveries.current[draftId]?.status : undefined
    const draft = draftId && (!draftStatus || draftStatus === 'rejected') ? getDraft(draftId) : undefined
    const closedDraft = draftStatus === 'sent' || draftStatus === 'uncertain' || draftStatus === 'compose'
    const history = turns.slice(-2).map(({ prompt, reply }) => ({
      prompt: Array.from(prompt).slice(0, 1000).join(''),
      answer: Array.from(reply.answer).slice(0, 1400).join(''),
    }))
    setPending(question)
    try {
      const reply = await askAssistant({
        prompt,
        history,
        message_id: message?.id,
        draft: closedDraft ? draftStatus : draft,
      }, controller.signal)
      if (!controller.signal.aborted) {
        if (reply.label_updates?.length) onLabelsUpdated(reply.label_updates)
        if (draftId && draft && reply.draft_delivery) {
          const to = draft.to.trim()
          setDelivery(draftId, reply.draft_delivery === 'sent'
            ? { status: 'sent', to }
            : { status: 'uncertain', to, message: uncertainDelivery })
          setReviewId(null)
        }
        setTurns((previous) => [...previous, { ...question, reply }])
        if (reply.actions?.some(({ kind }) => kind === 'draft')) setReviewId(null)
      }
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(reason instanceof HttpError
          ? reason.message
          : 'The connection was lost. If you requested a send, check Sent before retrying.')
      }
    } finally {
      if (request.current === controller) {
        request.current = null
        setPending(null)
      }
    }
  }

  return (
    <aside
      id="mail-assistant"
      className="assistant"
      aria-labelledby="assistant-title"
      hidden={!open}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !event.nativeEvent.isComposing) onClose()
      }}
    >
      <header className="assistant-header">
        <span className="assistant-mark" aria-hidden="true">✦</span>
        <h2 id="assistant-title">Mail assistant</h2>
        <button
          className="assistant-quiet"
          type="button"
          onClick={reset}
          disabled={busy || (!turns.length && !pending && !input && !error)}
        >
          New chat
        </button>
        <button className="assistant-close" type="button" onClick={onClose} aria-label="Close assistant">
          ×
        </button>
      </header>

      <div className="assistant-context">
        {selected ? (
          <label>
            <input
              type="checkbox"
              checked={includeMessage}
              onChange={(event) => setIncludeMessage(event.target.checked)}
            />
            Use open email
          </label>
        ) : <span>Inbox</span>}
        {message && <span className="assistant-subject" title={context}>{context}</span>}
      </div>

      <div ref={transcript} className="assistant-log" role="log" aria-label="Conversation">
        {!turns.length && !pending && (
          <p className="assistant-empty">Ask about your mail, plan how to organize it, or write a draft.</p>
        )}
        {turns.map((turn, index) => (
          <div className="assistant-turn" key={index}>
            <div className="assistant-question">
              <small>{turn.context}</small>
              <p>{turn.prompt}</p>
            </div>
            <div className="assistant-reply">
              <div className="assistant-answer">
                <Markdown skipHtml components={markdownComponents}>{turn.reply.answer}</Markdown>
              </div>
              {turn.reply.actions?.map((action, actionIndex) => {
                if (action.kind === 'draft') {
                  const id = `${index}:${actionIndex}`
                  const draft = editedDrafts[id] ?? { to: action.to ?? '', subject: action.subject, body: action.body }
                  return (
                    <AssistantDraftCard
                      key={actionIndex}
                      draft={draft}
                      delivery={deliveries[id]}
                      reviewing={reviewId === id}
                      sending={busy}
                      onReview={() => setReviewId(id)}
                      onCancel={() => setReviewId(null)}
                      onEdit={(edited) => setEditedDrafts((previous) => ({ ...previous, [id]: edited }))}
                      onSend={() => void sendDraft(id, draft)}
                      onUseDraft={() => {
                        if (!writingMail.current && !request.current && onUseDraft(draft)) {
                          setDelivery(id, { status: 'compose' })
                        }
                      }}
                    />
                  )
                }
                const isOpen = selected?.id === action.message_id
                const applied = isOpen && selected.labelIds.includes(action.label_id)
                return (
                  <div className="assistant-action assistant-label" key={actionIndex}>
                    <span>{action.label_name}</span>
                    {applied ? <small>Applied</small> : (
                      <button
                        className="assistant-chip"
                        type="button"
                        disabled={updating || busy}
                        onClick={() => {
                          setError('')
                          if (isOpen) void onChangeLabel(action.message_id, action.label_id, true, setError)
                          else void onOpenMessage(action.message_id, setError)
                        }}
                      >
                        {isOpen ? 'Apply label' : 'Open email'}
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        ))}
        {pending && (
          <div className="assistant-turn">
            <div className="assistant-question">
              <small>{pending.context}</small>
              <p>{pending.prompt}</p>
            </div>
            <p className="assistant-status" role="status">Working on your request…</p>
          </div>
        )}
        {error && <p className="error" role="alert">{error}</p>}
      </div>

      <form className="assistant-form" onSubmit={(event) => void submit(event)}>
        <div className="assistant-prompts" role="group" aria-label="Suggested prompts">
          {prompts.map(({ label, text }) => (
            <button
              key={label}
              className="assistant-chip"
              type="button"
              disabled={busy}
              onClick={() => {
                setInput(text)
                promptField.current?.focus()
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="sr-only" htmlFor="assistant-prompt">Ask your mail assistant</label>
        <div className="assistant-input">
          <textarea
            id="assistant-prompt"
            ref={promptField}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                event.currentTarget.form?.requestSubmit()
              }
            }}
            rows={2}
            maxLength={4000}
            readOnly={busy}
            placeholder="Ask about your mail…"
          />
          <button className="assistant-send" type="submit" disabled={busy || !input.trim()}>Send</button>
        </div>
        <p className="assistant-privacy">Prompts and relevant email text are processed by Groq.</p>
      </form>
    </aside>
  )
}
