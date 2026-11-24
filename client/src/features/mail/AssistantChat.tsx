import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { askAssistant } from './api'
import type { AssistantReply, MailDraft, MailMessage } from './types'
import './assistant.css'

type Question = { prompt: string; context: string }
type Exchange = Question & { reply: AssistantReply }
type ReportError = (message: string) => void

type AssistantChatProps = {
  open: boolean
  selected: MailMessage | null
  updating: boolean
  onClose: () => void
  onUseDraft: (draft: MailDraft) => void
  onApplyLabel: (messageId: string, labelId: string, reportError: ReportError) => void
  onOpenMessage: (messageId: string, reportError: ReportError) => Promise<void>
}

const inboxPrompts = [
  { label: 'Catch me up', text: 'Summarize my recent inbox and flag anything that needs a reply.' },
  { label: 'Find unread mail', text: 'Find my unread inbox emails and tell me who they’re from.' },
  { label: 'Organize inbox', text: 'Suggest how to organize my recent inbox using my Gmail labels.' },
  { label: 'Draft email', text: 'Draft an email to ' },
]
const messagePrompts = [
  { label: 'Summarize', text: 'Summarize this email, including any requests or deadlines.' },
  { label: 'Draft reply', text: 'Draft a concise reply to this email.' },
  { label: 'Suggest labels', text: 'Which of my existing Gmail labels would fit this email?' },
]

function historyAnswer(reply: AssistantReply): string {
  // Preserve the draft itself so requests such as “make it shorter” have context.
  const actions = (reply.actions ?? []).map((action) => action.kind === 'draft'
    ? `Draft to ${action.to || 'an unspecified recipient'}\nSubject: ${action.subject}\n${action.body}`
    : `Suggested Gmail label: ${action.label_name}`)
  return Array.from([reply.answer, ...actions].join('\n\n')).slice(0, 8000).join('')
}

export default function AssistantChat({
  open,
  selected,
  updating,
  onClose,
  onUseDraft,
  onApplyLabel,
  onOpenMessage,
}: AssistantChatProps) {
  const [includeMessage, setIncludeMessage] = useState(true)
  const [input, setInput] = useState('')
  const [turns, setTurns] = useState<Exchange[]>([])
  const [pending, setPending] = useState<Question | null>(null)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  const promptField = useRef<HTMLTextAreaElement>(null)
  const transcript = useRef<HTMLDivElement>(null)
  const message = includeMessage ? selected : null
  const context = message ? message.subject || '(No subject)' : 'Inbox'
  const prompts = message ? messagePrompts : inboxPrompts

  useEffect(() => () => request.current?.abort(), [])

  useEffect(() => {
    if (open) promptField.current?.focus()
  }, [open])

  useEffect(() => {
    const log = transcript.current
    if (open && log) log.scrollTop = log.scrollHeight
  }, [open, turns, pending, error])

  function cancel() {
    request.current?.abort()
    request.current = null
    if (pending) setInput(pending.prompt)
    setPending(null)
  }

  function reset() {
    cancel()
    setTurns([])
    setInput('')
    setError('')
    promptField.current?.focus()
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const prompt = input.trim()
    if (request.current || !prompt) return
    const controller = new AbortController()
    request.current = controller
    const question = { prompt, context }
    const history = turns.slice(-4).map((turn) => ({
      prompt: turn.prompt,
      answer: historyAnswer(turn.reply),
    }))
    setInput('')
    setError('')
    setPending(question)
    try {
      const reply = await askAssistant({
        prompt,
        history,
        message_id: message?.id,
      }, controller.signal)
      if (!controller.signal.aborted) {
        setTurns((previous) => [...previous, { ...question, reply }])
      }
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : 'Could not reach the assistant.')
        setInput(prompt)
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
          disabled={!turns.length && !pending && !input}
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
              <p className="assistant-answer">{turn.reply.answer}</p>
              {turn.reply.actions?.map((action, actionIndex) => {
                if (action.kind === 'draft') {
                  return (
                    <div className="assistant-action" key={actionIndex}>
                      <strong>{action.subject || '(No subject)'}</strong>
                      {action.to && <small>To {action.to}</small>}
                      <details>
                        <summary>Preview draft</summary>
                        <p>{action.body}</p>
                      </details>
                      <button className="assistant-chip" type="button" onClick={() => onUseDraft({
                        to: action.to ?? '',
                        subject: action.subject,
                        body: action.body,
                      })}>
                        Open in compose
                      </button>
                    </div>
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
                        disabled={updating}
                        onClick={() => {
                          setError('')
                          if (isOpen) onApplyLabel(action.message_id, action.label_id, setError)
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
              disabled={pending !== null}
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
            readOnly={pending !== null}
            placeholder="Ask about your mail…"
          />
          {pending ? (
            <button className="assistant-send" type="button" onClick={cancel}>Cancel</button>
          ) : (
            <button className="assistant-send" type="submit" disabled={!input.trim()}>Send</button>
          )}
        </div>
        <p className="assistant-privacy">Prompts and relevant email text are processed by Groq.</p>
      </form>
    </aside>
  )
}
