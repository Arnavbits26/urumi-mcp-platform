import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { duration, fullTime, relTime } from '../format.js'
import { ErrorNote, PageHead } from '../ui.jsx'

const OUTCOMES = {
  success: 'Succeeded',
  error: 'Failed',
  timeout: 'Timed out',
  unreachable: 'Server unreachable',
  rejected: 'Rejected',
}

export default function Activity({ me }) {
  const admin = me.role === 'admin'
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')
  const [outcome, setOutcome] = useState('')
  const [server, setServer] = useState('')
  const [person, setPerson] = useState('')

  const load = useCallback(async () => {
    try {
      setRows(await api.get(admin ? '/audit?limit=200' : '/me/audit?limit=200'))
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }, [admin])

  useEffect(() => {
    load()
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [load])

  const servers = useMemo(() => [...new Set((rows ?? []).map((r) => r.server).filter(Boolean))].sort(), [rows])
  const shown = (rows ?? []).filter(
    (r) => (!outcome || r.outcome === outcome) && (!server || r.server === server) && (!person || r.user_email.toLowerCase().includes(person.toLowerCase())),
  )

  return (
    <>
      <PageHead title={admin ? 'Audit log' : 'My activity'} />
      <p className="lede">
        {admin ? 'Every tool call made through the gateway, by anyone.' : 'Every tool call made with your keys or in the chat.'} The latest 200 are
        shown and the list refreshes every few seconds.
      </p>
      <div className="filters">
        <label>
          <span className="visually-hidden">Result</span>
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="">All results</option>
            {Object.entries(OUTCOMES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="visually-hidden">Server</span>
          <select value={server} onChange={(e) => setServer(e.target.value)}>
            <option value="">All servers</option>
            {servers.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        {admin && (
          <label>
            <span className="visually-hidden">Person</span>
            <input placeholder="Filter by email" value={person} onChange={(e) => setPerson(e.target.value)} />
          </label>
        )}
      </div>
      <ErrorNote>{error}</ErrorNote>
      {rows === null && !error && <p className="muted">Loading the log.</p>}
      {rows && shown.length === 0 && <p className="empty">{rows.length === 0 ? 'No tool calls yet.' : 'No calls match these filters.'}</p>}
      {shown.length > 0 && (
        <div className="scroll">
          <table className="table">
            <thead>
              <tr>
                <th>When</th>
                {admin && <th>Who</th>}
                <th>Tool</th>
                <th>Took</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.id}>
                  <td title={fullTime(r.ts)}>{relTime(r.ts)}</td>
                  {admin && <td>{r.user_email}</td>}
                  <td>
                    <code>{r.requested_name}</code>
                    {r.source === 'chat' && <span className="via">via chat</span>}
                  </td>
                  <td>{duration(r.duration_ms)}</td>
                  <td>
                    <span className={`result result-${r.outcome}`}>{OUTCOMES[r.outcome] ?? r.outcome}</span>
                    {r.outcome !== 'success' && r.detail && <span className="detail">{r.detail}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
