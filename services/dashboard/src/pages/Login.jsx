import React, { useState } from 'react'
import { api } from '../api.js'
import { ErrorNote, Field } from '../ui.jsx'

export default function Login({ onSignedIn }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      onSignedIn(await api.post('/auth/login', { email, password }))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login">
      <h1>Urumi gateway</h1>
      <p className="lede">Sign in to manage MCP servers and API keys, or to get your own key.</p>
      <form onSubmit={submit} className="stack">
        <Field label="Email">
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password">
          <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorNote>{error}</ErrorNote>
        <div className="actions actions-start">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Signing in' : 'Sign in'}
          </button>
        </div>
      </form>
    </main>
  )
}
