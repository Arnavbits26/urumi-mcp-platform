import React, { useEffect, useState } from 'react'
import { api, onUnauthorized } from './api.js'
import Login from './pages/Login.jsx'
import Servers from './pages/Servers.jsx'
import Team from './pages/Team.jsx'
import KeysPage from './pages/Keys.jsx'
import Activity from './pages/Activity.jsx'
import Chat from './pages/Chat.jsx'

function useHashRoute() {
  const read = () => window.location.hash.replace(/^#\/?/, '')
  const [route, setRoute] = useState(read)
  useEffect(() => {
    const onChange = () => setRoute(read())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export default function App() {
  const [me, setMe] = useState(undefined) // undefined: still checking, null: signed out
  const route = useHashRoute()

  useEffect(() => {
    onUnauthorized(() => setMe(null))
    api.get('/me').then(setMe, () => setMe(null))
  }, [])

  if (me === undefined) return <p className="boot muted">Loading.</p>
  if (me === null) return <Login onSignedIn={setMe} />

  const admin = me.role === 'admin'
  const pages = [
    ...(admin ? [{ id: 'servers', label: 'Servers' }, { id: 'team', label: 'Team' }] : []),
    { id: 'chat', label: 'Chat' },
    { id: 'keys', label: 'API keys' },
    { id: 'activity', label: admin ? 'Audit log' : 'My activity' },
  ]
  const current = pages.find((p) => p.id === route) ?? pages[0]

  async function signOut() {
    try {
      await api.post('/auth/logout')
    } finally {
      setMe(null)
    }
  }

  return (
    <div className="shell">
      <nav className="nav" aria-label="Main">
        <p className="brand">Urumi gateway</p>
        <ul>
          {pages.map((p) => (
            <li key={p.id}>
              <a href={`#/${p.id}`} aria-current={p.id === current.id ? 'page' : undefined}>
                {p.label}
              </a>
            </li>
          ))}
        </ul>
        <div className="who">
          <p>{me.name || me.email}</p>
          <p className="muted">{admin ? 'Admin' : 'Member'}</p>
          <button type="button" className="btn btn-quiet" onClick={signOut}>
            Sign out
          </button>
        </div>
      </nav>
      <main className="content">
        {current.id === 'servers' && <Servers />}
        {current.id === 'team' && <Team me={me} />}
        {current.id === 'chat' && <Chat me={me} />}
        {current.id === 'keys' && <KeysPage />}
        {current.id === 'activity' && <Activity me={me} />}
      </main>
    </div>
  )
}
