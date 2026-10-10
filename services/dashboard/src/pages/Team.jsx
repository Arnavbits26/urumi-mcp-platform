import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import { relTime } from '../format.js'
import { Confirm, ErrorNote, Field, Modal, PageHead, SecretsModal } from '../ui.jsx'

function InviteForm({ onClose, onInvited }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState('member')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      onInvited(await api.post('/users', { email, name, role }))
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <Modal title="Invite a team member" onClose={onClose}>
      <form onSubmit={submit} className="stack">
        <Field label="Email">
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Name">
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Role" hint="Members use the tools. Admins also manage servers and people.">
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="member">Member</option>
            <option value="admin">Admin</option>
          </select>
        </Field>
        <ErrorNote>{error}</ErrorNote>
        <div className="actions">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Create account
          </button>
        </div>
      </form>
    </Modal>
  )
}

function KeyList({ user, onSecret }) {
  const [keys, setKeys] = useState(null)
  const [label, setLabel] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setKeys(await api.get(`/users/${user.id}/keys`))
    } catch (err) {
      setError(err.message)
    }
  }, [user.id])
  useEffect(() => {
    load()
  }, [load])

  async function issue(e) {
    e.preventDefault()
    setError('')
    try {
      const key = await api.post(`/users/${user.id}/keys`, { name: label })
      setLabel('')
      onSecret({
        title: `New key for ${user.email}`,
        intro: 'This key is shown once. The gateway keeps only a fingerprint of it, so it cannot be shown again.',
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
    <div className="subpanel">
      <h4>API keys</h4>
      {keys === null && !error && <p className="muted">Loading keys.</p>}
      {keys && keys.length === 0 && <p className="muted">No keys yet.</p>}
      {keys && keys.length > 0 && (
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Key</th>
              <th>Name</th>
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
      <form onSubmit={issue} className="inline-form">
        <input aria-label="Name for the new key" placeholder="Name, for example laptop" value={label} onChange={(e) => setLabel(e.target.value)} />
        <button type="submit" className="btn btn-secondary">
          Issue new key
        </button>
      </form>
      <ErrorNote>{error}</ErrorNote>
    </div>
  )
}

export default function Team({ me }) {
  const [users, setUsers] = useState(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(null)
  const [dialog, setDialog] = useState(null)
  const [secret, setSecret] = useState(null)
  const [confirmError, setConfirmError] = useState('')

  const load = useCallback(async () => {
    try {
      setUsers(await api.get('/users'))
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }, [])
  useEffect(() => {
    load()
  }, [load])

  async function update(user, patch) {
    setError('')
    try {
      await api.patch(`/users/${user.id}`, patch)
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function resetPassword() {
    try {
      const out = await api.post(`/users/${dialog.user.id}/reset-password`)
      setSecret({
        title: `New password for ${dialog.user.email}`,
        intro: 'Share this with them now. It is shown once, and they have been signed out everywhere.',
        items: [{ label: 'Temporary password', value: out.temporary_password }],
      })
      setDialog(null)
      setConfirmError('')
    } catch (err) {
      setConfirmError(err.message)
    }
  }

  return (
    <>
      <PageHead title="Team">
        <button type="button" className="btn btn-primary" onClick={() => setDialog({ mode: 'invite' })}>
          Invite member
        </button>
      </PageHead>
      <ErrorNote>{error}</ErrorNote>
      {users === null && !error && <p className="muted">Loading team.</p>}
      {users && (
        <ul className="people">
          {users.map((u) => (
            <li key={u.id} className="person">
              <div className="person-main">
                <div>
                  <h3>{u.name || u.email}</h3>
                  {u.name && <p className="muted">{u.email}</p>}
                  {u.disabled && <p className="reason">Account disabled. They cannot sign in or use any key.</p>}
                </div>
                <label className="role">
                  <span className="visually-hidden">Role for {u.email}</span>
                  <select value={u.role} disabled={u.id === me.id} onChange={(e) => update(u, { role: e.target.value })}>
                    <option value="member">Member</option>
                    <option value="admin">Admin</option>
                  </select>
                </label>
                <div className="person-actions">
                  <button type="button" className="btn btn-quiet" aria-expanded={open === u.id} onClick={() => setOpen(open === u.id ? null : u.id)}>
                    {open === u.id ? 'Hide keys' : 'Keys'}
                  </button>
                  <button type="button" className="btn btn-quiet" onClick={() => setDialog({ mode: 'reset', user: u })}>
                    Reset password
                  </button>
                  {u.id !== me.id && (
                    <button type="button" className="btn btn-quiet" onClick={() => update(u, { disabled: !u.disabled })}>
                      {u.disabled ? 'Enable' : 'Disable'}
                    </button>
                  )}
                </div>
              </div>
              {open === u.id && <KeyList user={u} onSecret={setSecret} />}
            </li>
          ))}
        </ul>
      )}

      {dialog?.mode === 'invite' && (
        <InviteForm
          onClose={() => setDialog(null)}
          onInvited={(out) => {
            setDialog(null)
            setSecret({
              title: `${out.user.email} is invited`,
              intro: 'Give them both of these now. They are shown once. They sign in with the password, and use the key in their AI client.',
              items: [
                { label: 'Temporary password', value: out.temporary_password },
                { label: 'API key', value: out.key.api_key },
              ],
            })
            load()
          }}
        />
      )}
      {dialog?.mode === 'reset' && (
        <Confirm
          title={`Reset the password for ${dialog.user.email}?`}
          confirmLabel="Reset password"
          cancelLabel="Keep current password"
          onConfirm={resetPassword}
          onClose={() => setDialog(null)}
          error={confirmError}
        >
          <p>They will be signed out everywhere and will need the new temporary password. Their API keys keep working.</p>
        </Confirm>
      )}
      {secret && <SecretsModal {...secret} onClose={() => setSecret(null)} />}
    </>
  )
}
