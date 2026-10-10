import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import { relTime, plural, slugify } from '../format.js'
import { Confirm, CopyButton, ErrorNote, Field, Modal, PageHead, StatusPill } from '../ui.jsx'

const AUTH_LABELS = { none: 'No credential', bearer: 'Bearer token', header: 'Custom header' }

function ServerForm({ server, onClose, onSaved }) {
  const editing = Boolean(server)
  const [name, setName] = useState(server?.name ?? '')
  const [slug, setSlug] = useState(server?.slug ?? '')
  const [slugTouched, setSlugTouched] = useState(false)
  const [url, setUrl] = useState(server?.url ?? '')
  const [authType, setAuthType] = useState(server?.auth_type ?? 'none')
  const [headerName, setHeaderName] = useState(server?.auth_header_name ?? '')
  const [credential, setCredential] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    const body = { name, url, auth_type: authType }
    if (authType === 'header') body.auth_header_name = headerName
    if (credential) body.credential = credential
    try {
      if (editing) await api.patch(`/servers/${server.id}`, body)
      else await api.post('/servers', { ...body, slug })
      onSaved()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={editing ? `Edit ${server.name}` : 'Add a server'} onClose={onClose}>
      <form onSubmit={submit} className="stack">
        <Field label="Name">
          <input
            required
            value={name}
            onChange={(e) => {
              setName(e.target.value)
              if (!editing && !slugTouched) setSlug(slugify(e.target.value))
            }}
          />
        </Field>
        <Field label="Short name for tools" hint={editing ? 'Fixed, because tool names are built from it.' : 'Lowercase letters, numbers and hyphens. Tools will be named like shortname__toolname.'}>
          <input
            required
            readOnly={editing}
            pattern="[a-z][a-z0-9\-]{1,23}"
            value={slug}
            onChange={(e) => {
              setSlugTouched(true)
              setSlug(e.target.value)
            }}
          />
        </Field>
        <Field label="Server URL" hint="The MCP endpoint, for example http://my-server.my-namespace.svc.cluster.local:8000/mcp">
          <input required type="url" value={url} onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <Field label="How the gateway signs in">
          <select value={authType} onChange={(e) => setAuthType(e.target.value)}>
            {Object.entries(AUTH_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        {authType === 'header' && (
          <Field label="Header name">
            <input required value={headerName} onChange={(e) => setHeaderName(e.target.value)} placeholder="X-Api-Key" />
          </Field>
        )}
        {authType !== 'none' && (
          <Field
            label={authType === 'bearer' ? 'Token' : 'Header value'}
            hint={editing && server.has_credential ? 'Leave blank to keep the stored one.' : 'Stored encrypted. It is never shown again and never sent to team members.'}
          >
            <input type="password" autoComplete="off" required={!editing || !server.has_credential} value={credential} onChange={(e) => setCredential(e.target.value)} />
          </Field>
        )}
        <ErrorNote>{error}</ErrorNote>
        <div className="actions">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {editing ? 'Save changes' : 'Add server'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

function ServerRow({ server, expanded, onToggle, onEdit, onRemove, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function act(fn) {
    setBusy(true)
    setError('')
    try {
      await fn()
      await onChanged()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="server">
      <div className="server-main">
        <div className="server-id">
          <StatusPill status={server.status} />
          <h3>{server.name}</h3>
          <code>{server.slug}</code>
        </div>
        <p className="server-url">
          <code>{server.url}</code>
        </p>
        <p className="server-meta">
          {server.status === 'unreachable' && server.status_detail && <span className="reason">{server.status_detail}. </span>}
          {server.status === 'disabled' ? 'Tools are hidden from everyone.' : `${plural(server.tools.length, 'tool')}, checked ${relTime(server.last_checked_at)}.`}
          {` ${AUTH_LABELS[server.auth_type]}.`}
        </p>
        <div className="server-actions">
          <button type="button" className="btn btn-quiet" onClick={onToggle} aria-expanded={expanded} disabled={server.tools.length === 0}>
            {expanded ? 'Hide tools' : 'Show tools'}
          </button>
          <button type="button" className="btn btn-quiet" onClick={() => act(() => api.post(`/servers/${server.id}/refresh`))} disabled={busy || !server.enabled}>
            Check now
          </button>
          <button type="button" className="btn btn-quiet" onClick={() => act(() => api.patch(`/servers/${server.id}`, { enabled: !server.enabled }))} disabled={busy}>
            {server.enabled ? 'Disable' : 'Enable'}
          </button>
          <button type="button" className="btn btn-quiet" onClick={onEdit}>
            Edit
          </button>
          <button type="button" className="btn btn-quiet btn-danger-text" onClick={onRemove}>
            Remove
          </button>
        </div>
      </div>
      <ErrorNote>{error}</ErrorNote>
      {expanded && (
        <ul className="tools">
          {server.tools.map((t) => (
            <li key={t.name}>
              <code>{t.public_name}</code>
              <span>{t.description}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

export default function Servers() {
  const [servers, setServers] = useState(null)
  const [error, setError] = useState('')
  const [dialog, setDialog] = useState(null)
  const [expanded, setExpanded] = useState({})
  const [removeError, setRemoveError] = useState('')
  const endpoint = `${window.location.origin}/mcp`

  const load = useCallback(async () => {
    try {
      setServers(await api.get('/servers'))
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => {
    load()
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [load])

  const connected = (servers ?? []).filter((s) => s.status === 'connected')
  const toolCount = connected.reduce((n, s) => n + s.tools.length, 0)

  async function remove() {
    try {
      await api.del(`/servers/${dialog.server.id}`)
      setDialog(null)
      setRemoveError('')
      await load()
    } catch (err) {
      setRemoveError(err.message)
    }
  }

  return (
    <>
      <PageHead title="Servers">
        <button type="button" className="btn btn-primary" onClick={() => setDialog({ mode: 'add' })}>
          Add server
        </button>
      </PageHead>

      <section className="endpoint" aria-label="Gateway address">
        <p>Your team connects to one address. Everything below sits behind it.</p>
        <div className="endpoint-row">
          <code>{endpoint}</code>
          <CopyButton value={endpoint} label="Copy address" />
        </div>
        {servers && servers.length > 0 && (
          <p className="endpoint-count">
            {connected.length} of {plural(servers.length, 'server')} connected, offering {plural(toolCount, 'tool')}.
          </p>
        )}
      </section>

      <ErrorNote>{error}</ErrorNote>
      {servers === null && !error && <p className="muted">Loading servers.</p>}
      {servers && servers.length === 0 && (
        <p className="empty">No servers yet. Add the first MCP server your team should be able to use.</p>
      )}
      {servers && servers.length > 0 && (
        <ul className="servers">
          {servers.map((s) => (
            <ServerRow
              key={s.id}
              server={s}
              expanded={Boolean(expanded[s.id])}
              onToggle={() => setExpanded((e) => ({ ...e, [s.id]: !e[s.id] }))}
              onEdit={() => setDialog({ mode: 'edit', server: s })}
              onRemove={() => {
                setRemoveError('')
                setDialog({ mode: 'remove', server: s })
              }}
              onChanged={load}
            />
          ))}
        </ul>
      )}

      {dialog?.mode === 'add' && (
        <ServerForm
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null)
            load()
          }}
        />
      )}
      {dialog?.mode === 'edit' && (
        <ServerForm
          server={dialog.server}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null)
            load()
          }}
        />
      )}
      {dialog?.mode === 'remove' && (
        <Confirm
          title={`Remove ${dialog.server.name}?`}
          confirmLabel="Remove server"
          cancelLabel="Keep server"
          onConfirm={remove}
          onClose={() => setDialog(null)}
          error={removeError}
        >
          <p>
            Its {plural(dialog.server.tools.length, 'tool')} will disappear for everyone, including the chat and external clients. The stored
            credential is deleted.
          </p>
        </Confirm>
      )}
    </>
  )
}
