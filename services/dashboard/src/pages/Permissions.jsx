import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import { plural } from '../format.js'
import { Confirm, ErrorNote, PageHead, StatusPill } from '../ui.jsx'

const ROLE_LABELS = { member: 'Members', admin: 'Admins' }
const SOURCE_LABELS = { tool: 'set for this tool', server: 'set for the whole server', default: 'default' }
// Tools whose names start with one of these verbs change something, so "read-only" blocks them.
const WRITE_VERB = /^(create|update|delete|remove|add|set|write|send|post|put|patch|upload|edit|modify|cancel|refund)(_|$)/i

const isWrite = (tool) => WRITE_VERB.test(tool.name)

function Cell({ tool, role, busy, onSet, onReset }) {
  const { allowed, source } = tool.access[role]
  return (
    <td>
      <button
        type="button"
        className={`perm perm-${allowed ? 'allowed' : 'blocked'}`}
        aria-label={`${ROLE_LABELS[role]}, ${tool.public_name}: ${allowed ? 'allowed' : 'blocked'}. Click to ${allowed ? 'block' : 'allow'}.`}
        onClick={() => onSet(allowed ? false : true)}
        disabled={busy}
      >
        {allowed ? 'Allowed' : 'Blocked'}
      </button>
      <span className="perm-source">
        {SOURCE_LABELS[source]}
        {source === 'tool' && (
          <button type="button" className="btn btn-quiet perm-reset" onClick={onReset} disabled={busy} aria-label={`Reset ${ROLE_LABELS[role]} rule for ${tool.public_name}`}>
            Reset
          </button>
        )}
      </span>
    </td>
  )
}

function ServerSection({ server, busy, onChange, onAsk }) {
  const memberWrites = server.tools.filter((t) => isWrite(t) && t.access.member.allowed)
  const memberRules = [
    ...(server.server_rules.member !== null ? [{ role: 'member', server: server.slug, tool: '*', allow: null }] : []),
    ...server.tools.filter((t) => t.access.member.source === 'tool').map((t) => ({ role: 'member', server: server.slug, tool: t.name, allow: null })),
  ]
  return (
    <section className="perm-server" aria-label={server.name}>
      <header className="perm-head">
        <div className="server-id">
          <StatusPill status={server.status} />
          <h2>{server.name}</h2>
          <code>{server.slug}</code>
        </div>
        <div className="server-actions">
          <button type="button" className="btn btn-secondary" disabled={busy || memberWrites.length === 0} onClick={() => onAsk({ kind: 'readonly', server, tools: memberWrites })}>
            Make read-only for members
          </button>
          <button type="button" className="btn btn-quiet" disabled={busy || server.server_rules.member === false} onClick={() => onAsk({ kind: 'blockall', server })}>
            Block for members
          </button>
          <button type="button" className="btn btn-quiet" disabled={busy || memberRules.length === 0} onClick={() => onChange(memberRules)}>
            Reset member rules
          </button>
        </div>
      </header>
      {server.server_rules.member === false && <p className="note">Everything on this server is blocked for members, except tools you allow one by one below.</p>}
      {server.tools.length === 0 ? (
        <p className="muted">No tools are listed right now{server.status === 'connected' ? '' : ` (the server is ${server.status})`}. Rules you set earlier apply again when its tools return.</p>
      ) : (
        <table className="perm-table">
          <thead>
            <tr>
              <th scope="col">Tool</th>
              <th scope="col">{ROLE_LABELS.member}</th>
              <th scope="col">{ROLE_LABELS.admin}</th>
            </tr>
          </thead>
          <tbody>
            {server.tools.map((t) => (
              <tr key={t.name}>
                <th scope="row">
                  <code>{t.public_name}</code>
                  <span className="perm-desc">{t.description}</span>
                </th>
                {['member', 'admin'].map((role) => (
                  <Cell
                    key={role}
                    tool={t}
                    role={role}
                    busy={busy}
                    onSet={(allow) => onChange([{ role, server: server.slug, tool: t.name, allow }])}
                    onReset={() => onChange([{ role, server: server.slug, tool: t.name, allow: null }])}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

export default function Permissions() {
  const [matrix, setMatrix] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [ask, setAsk] = useState(null)

  const load = useCallback(async () => {
    try {
      setMatrix(await api.get('/permissions'))
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => {
    load()
    const timer = setInterval(load, 15000)
    return () => clearInterval(timer)
  }, [load])

  async function change(changes) {
    setBusy(true)
    setError('')
    try {
      setMatrix(await api.put('/permissions', { changes }))
      setAsk(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const blocked = (matrix?.servers ?? []).reduce(
    (n, s) => n + s.tools.reduce((m, t) => m + ['member', 'admin'].filter((r) => !t.access[r].allowed).length, 0),
    0,
  )

  return (
    <>
      <PageHead title="Permissions" />
      <section className="endpoint" aria-label="How permissions work">
        <p>
          Choose which tools each role may use. Everything is allowed until you block it. A blocked tool disappears from the list that members' AI clients and the chat see, and
          a direct call to it is refused and recorded in the audit log.
        </p>
        {matrix && <p className="endpoint-count">{blocked === 0 ? 'Nothing is blocked.' : `${plural(blocked, 'permission')} blocked.`}</p>}
      </section>

      <ErrorNote>{error}</ErrorNote>
      {matrix === null && !error && <p className="muted">Loading permissions.</p>}
      {matrix && matrix.servers.length === 0 && <p className="empty">No servers yet. Add one on the Servers page first.</p>}
      {matrix?.servers.map((s) => (
        <ServerSection key={s.slug} server={s} busy={busy} onChange={change} onAsk={setAsk} />
      ))}

      {ask?.kind === 'readonly' && (
        <Confirm
          title={`Make ${ask.server.name} read-only for members?`}
          confirmLabel="Block these tools for members"
          cancelLabel="Cancel"
          busy={busy}
          error={error}
          onClose={() => setAsk(null)}
          onConfirm={() => change(ask.tools.map((t) => ({ role: 'member', server: ask.server.slug, tool: t.name, allow: false })))}
        >
          <p>Members will no longer see or be able to use these {plural(ask.tools.length, 'tool')}. Admins are not affected.</p>
          <ul className="perm-list">
            {ask.tools.map((t) => (
              <li key={t.name}>
                <code>{t.public_name}</code>
              </li>
            ))}
          </ul>
        </Confirm>
      )}
      {ask?.kind === 'blockall' && (
        <Confirm
          title={`Block ${ask.server.name} for members?`}
          confirmLabel="Block for members"
          cancelLabel="Cancel"
          busy={busy}
          error={error}
          onClose={() => setAsk(null)}
          onConfirm={() => change([{ role: 'member', server: ask.server.slug, tool: '*', allow: false }])}
        >
          <p>Members will not see or use any tool of this server. You can still allow single tools afterwards. Admins are not affected.</p>
        </Confirm>
      )}
    </>
  )
}
