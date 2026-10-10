import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { duration, plural } from '../format.js'
import { ErrorNote, PageHead } from '../ui.jsx'

const STARTERS = ['What orders came in today?', 'List the products in the store', 'Create a product called Test mug priced at 499']
const OUTCOME = { success: 'Succeeded', error: 'Failed', timeout: 'Timed out', unreachable: 'Server unreachable', rejected: 'Rejected' }

function ToolCalls({ calls }) {
  const [open, setOpen] = useState(false)
  if (!calls?.length) return null
  return (
    <div className="toolcalls">
      <p className="toolcalls-title">Used {plural(calls.length, 'tool')}</p>
      <ul>
        {calls.map((c, i) => (
          <li key={i}>
            <code>{c.name}</code>
            <span className={`result result-${c.outcome}`}>{OUTCOME[c.outcome] ?? c.outcome}</span>
            <span className="muted">{duration(c.duration_ms)}</span>
          </li>
        ))}
      </ul>
      <button type="button" className="btn btn-quiet" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? 'Hide details' : 'Show details'}
      </button>
      {open &&
        calls.map((c, i) => (
          <div key={i} className="toolcall-detail">
            <h4>
              <code>{c.name}</code>
            </h4>
            <p className="muted">Arguments</p>
            <pre>{JSON.stringify(c.arguments, null, 2)}</pre>
            <p className="muted">Result</p>
            <pre>{c.preview}</pre>
          </div>
        ))}
    </div>
  )
}

export default function Chat({ me }) {
  const [status, setStatus] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const end = useRef(null)

  useEffect(() => {
    api.get('/chat/status').then(setStatus, (err) => setError(err.message))
  }, [])
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'end' })
  }, [messages, busy])

  async function send(text) {
    const trimmed = text.trim()
    if (!trimmed || busy) return
    const next = [...messages, { role: 'user', content: trimmed }]
    setMessages(next)
    setInput('')
    setBusy(true)
    setError('')
    try {
      const out = await api.post('/chat', { messages: next.slice(-20).map(({ role, content }) => ({ role, content })) })
      setMessages([...next, { role: 'assistant', content: out.reply, tool_calls: out.tool_calls }])
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const ready = status?.configured
  return (
    <>
      <PageHead title="Chat">
        {messages.length > 0 && (
          <button type="button" className="btn btn-secondary" onClick={() => { setMessages([]); setError('') }}>
            New chat
          </button>
        )}
      </PageHead>
      <p className="lede">
        Ask in plain words. The assistant uses the same tools as your AI client, and every call it makes is recorded in the audit log under your name.
      </p>

      {status && !ready && (
        <p className="note">
          The chat is not set up yet.{' '}
          {me.role === 'admin' ? 'Add an AI service key by running scripts/llm-secret.sh, then reload this page.' : 'Ask an admin to add an AI service key.'}
        </p>
      )}

      {messages.length === 0 && ready && (
        <section className="starters" aria-label="Ideas to try">
          <p className="muted">Try one of these, or write your own.</p>
          <ul>
            {STARTERS.map((s) => (
              <li key={s}>
                <button type="button" className="btn btn-secondary" onClick={() => send(s)} disabled={busy}>
                  {s}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {messages.length > 0 && (
        <ol className="turns" aria-label="Conversation">
          {messages.map((m, i) => (
            <li key={i} className="turn">
              <p className="speaker">{m.role === 'user' ? 'You' : 'Assistant'}</p>
              <p className="said">{m.content}</p>
              {m.role === 'assistant' && <ToolCalls calls={m.tool_calls} />}
            </li>
          ))}
        </ol>
      )}
      {busy && (
        <p className="muted working" role="status">
          Working on it. The assistant may call a few tools first.
        </p>
      )}
      <ErrorNote>{error}</ErrorNote>
      <div ref={end} />

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
      >
        <label className="visually-hidden" htmlFor="chat-input">
          Your message
        </label>
        <textarea
          id="chat-input"
          rows={2}
          placeholder="Ask about orders or products, or ask for a change"
          value={input}
          disabled={!ready}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send(input)
            }
          }}
        />
        <button type="submit" className="btn btn-primary" disabled={!ready || busy || !input.trim()}>
          Send
        </button>
      </form>
    </>
  )
}
