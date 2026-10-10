import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import { relTime } from '../format.js'
import { ErrorNote, Field, PageHead, Secret, SecretsModal } from '../ui.jsx'

function Connect() {
  const url = `${window.location.origin}/mcp`
  const claude = `claude mcp add --transport http urumi ${url} --header "Authorization: Bearer YOUR_API_KEY"`
  const json = JSON.stringify({ mcpServers: { urumi: { url, headers: { Authorization: 'Bearer YOUR_API_KEY' } } } }, null, 2)
  return (
    <section className="connect">
      <h2>Connect your AI client</h2>
      <p>
        Add this one address and your own key. You then see the tools of every enabled server, and you never need the servers' own credentials.
      </p>
      <Secret label="Address" value={url} />
      <Secret label="Claude Code" value={claude} />
      <Secret label="Cursor, in .cursor/mcp.json" value={json} />
    </section>
  )
}

function Keys() {
  const [keys, setKeys] = useState(null)
  const [name, setName] = useState('')
  const [secret, setSecret] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setKeys(await api.get('/me/keys'))
    } catch (err) {
      setError(err.message)
    }
  }, [])
  useEffect(() => {
    load()
  }, [load])

  async function create(e) {
    e.preventDefault()
    setError('')
    try {
      const key = await api.post('/me/keys', { name })
      setName('')
      setSecret({
        title: 'Your new API key',
        intro: 'Copy it into your AI client now. It is shown once, because the gateway keeps only a fingerprint of it.',
        items: [{ label: 'API key', value: key.api_key }],
      })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function revoke(id) {
    setError('')
    try {
      await api.del(`/keys/${id}`)
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <h2>Your API keys</h2>
      {keys === null && !error && <p className="muted">Loading keys.</p>}
      {keys && keys.length === 0 && <p className="empty">You have no keys yet. Create one to connect an AI client.</p>}
      {keys && keys.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Key</th>
              <th>Name</th>
              <th>Created</th>
              <th>Last used</th>
              <th>State</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k.id}>
                <td>
                  <code>urm_{k.prefix}_…</code>
                </td>
                <td>{k.name || '—'}</td>
                <td>{relTime(k.created_at)}</td>
                <td>{relTime(k.last_used_at)}</td>
                <td>{k.revoked_at ? 'Revoked' : 'Active'}</td>
                <td className="cell-end">
                  {!k.revoked_at && (
                    <button type="button" className="btn btn-quiet btn-danger-text" onClick={() => revoke(k.id)}>
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form onSubmit={create} className="inline-form">
        <input aria-label="Name for the new key" placeholder="Name, for example laptop" value={name} onChange={(e) => setName(e.target.value)} />
        <button type="submit" className="btn btn-primary">
          Create key
        </button>
      </form>
      <ErrorNote>{error}</ErrorNote>
      {secret && <SecretsModal {...secret} onClose={() => setSecret(null)} />}
    </section>
  )
}

function ChangePassword() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setDone(false)
    if (next !== again) return setError('The two new passwords do not match.')
    setError('')
    try {
      await api.post('/me/password', { current_password: current, new_password: next })
      setCurrent('')
      setNext('')
      setAgain('')
      setDone(true)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <h2>Change your password</h2>
      <form onSubmit={submit} className="stack narrow">
        <Field label="Current password">
          <input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </Field>
        <Field label="New password" hint="At least 10 characters.">
          <input type="password" autoComplete="new-password" required minLength={10} value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label="New password again">
          <input type="password" autoComplete="new-password" required value={again} onChange={(e) => setAgain(e.target.value)} />
        </Field>
        <ErrorNote>{error}</ErrorNote>
        {done && (
          <p className="note" role="status">
            Password changed.
          </p>
        )}
        <div className="actions actions-start">
          <button type="submit" className="btn btn-secondary">
            Change password
          </button>
        </div>
      </form>
    </section>
  )
}

export default function KeysPage() {
  return (
    <>
      <PageHead title="API keys" />
      <Connect />
      <Keys />
      <ChangePassword />
    </>
  )
}
