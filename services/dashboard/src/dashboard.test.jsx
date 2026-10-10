import React from 'react'
import { render, screen, within, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'
import { api, onUnauthorized } from './api.js'

const ADMIN = { id: 'u1', email: 'admin@example.com', name: 'Admin', role: 'admin' }
const MEMBER = { id: 'u2', email: 'bob@example.com', name: 'Bob', role: 'member' }

const tool = (name, description) => ({ name, public_name: `woocommerce__${name}`, description })
const SERVERS = [
  {
    id: 's1', slug: 'woocommerce', name: 'WooCommerce', url: 'http://wc-mcp.store.svc:8000/mcp', auth_type: 'none', auth_header_name: null,
    has_credential: false, enabled: true, status: 'connected', status_detail: null, last_checked_at: new Date().toISOString(),
    tools: ['list_products', 'create_product', 'list_orders', 'get_order', 'update_order_status'].map((n) => tool(n, `${n} description`)),
  },
  {
    id: 's2', slug: 'notes', name: 'Notes', url: 'http://notes.apps.svc:8000/mcp', auth_type: 'bearer', auth_header_name: null,
    has_credential: true, enabled: true, status: 'unreachable', status_detail: 'connection refused or host unreachable',
    last_checked_at: new Date().toISOString(), tools: [],
  },
  {
    id: 's3', slug: 'clock', name: 'Clock', url: 'http://clock.apps.svc:8000/mcp', auth_type: 'none', auth_header_name: null,
    has_credential: false, enabled: false, status: 'disabled', status_detail: null, last_checked_at: null, tools: [],
  },
]

/** A fake gateway. Each route returns [status, body]; every call is recorded. */
function fakeGateway(routes) {
  const calls = []
  vi.stubGlobal('fetch', vi.fn(async (url, opts = {}) => {
    const method = opts.method ?? 'GET'
    calls.push({ method, url, headers: opts.headers ?? {}, body: opts.body ? JSON.parse(opts.body) : undefined })
    const handler = routes[`${method} ${url}`]
    if (!handler) return { ok: false, status: 404, json: async () => ({ detail: `no route ${method} ${url}` }) }
    const [status, body] = typeof handler === 'function' ? handler(calls.at(-1)) : handler
    return { ok: status < 400, status, json: async () => body }
  }))
  return calls
}

beforeEach(() => { window.location.hash = '' })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('api wrapper', () => {
  it('adds the CSRF header to changes but not to reads', async () => {
    const calls = fakeGateway({ 'GET /api/me': [200, ADMIN], 'POST /api/x': [200, {}] })
    await api.get('/me')
    await api.post('/x', { a: 1 })
    expect(calls[0].headers['X-Requested-With']).toBeUndefined()
    expect(calls[1].headers['X-Requested-With']).toBe('urumi-dashboard')
  })

  it('reports a 401 (except at login) so the app can show the sign-in page', async () => {
    fakeGateway({ 'GET /api/servers': [401, { detail: 'not signed in' }], 'POST /api/auth/login': [401, { detail: 'Incorrect email or password.' }] })
    const handler = vi.fn()
    onUnauthorized(handler)
    await expect(api.get('/servers')).rejects.toThrow('not signed in')
    expect(handler).toHaveBeenCalledTimes(1)
    await expect(api.post('/auth/login', {})).rejects.toThrow('Incorrect email or password.')
    expect(handler).toHaveBeenCalledTimes(1)
  })
})

describe('sign in', () => {
  it('shows the server message on a wrong password, then signs in', async () => {
    let attempts = 0
    fakeGateway({
      'GET /api/me': [401, { detail: 'not signed in' }],
      'POST /api/auth/login': () => (++attempts === 1 ? [401, { detail: 'Incorrect email or password.' }] : [200, MEMBER]),
      'GET /api/chat/status': [200, { configured: true, model: 'test-model' }],
    })
    const user = userEvent.setup()
    render(<App />)
    await user.type(await screen.findByLabelText('Email'), 'bob@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect email or password.')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { name: 'Chat' })).toBeInTheDocument()
  })
})

describe('what each role sees', () => {
  it('a member gets chat, keys and their own activity, not servers or team', async () => {
    fakeGateway({ 'GET /api/me': [200, MEMBER], 'GET /api/chat/status': [200, { configured: true, model: 'm' }] })
    render(<App />)
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).getByText('Chat')).toBeInTheDocument()
    expect(within(nav).getByText('API keys')).toBeInTheDocument()
    expect(within(nav).getByText('My activity')).toBeInTheDocument()
    expect(within(nav).queryByText('Servers')).toBeNull()
    expect(within(nav).queryByText('Team')).toBeNull()
  })

  it('an admin gets servers, team and the audit log', async () => {
    fakeGateway({ 'GET /api/me': [200, ADMIN], 'GET /api/servers': [200, SERVERS] })
    render(<App />)
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    for (const label of ['Servers', 'Team', 'Chat', 'API keys', 'Audit log']) expect(within(nav).getByText(label)).toBeInTheDocument()
  })
})

describe('servers', () => {
  async function openServers(extra = {}) {
    const calls = fakeGateway({ 'GET /api/me': [200, ADMIN], 'GET /api/servers': [200, SERVERS], ...extra })
    render(<App />)
    await screen.findByRole('heading', { name: 'Servers' })
    return calls
  }

  it('summarises health and shows each status with its reason', async () => {
    await openServers()
    expect(await screen.findByText('1 of 3 servers connected, offering 5 tools.')).toBeInTheDocument()
    expect(screen.getByText('Connected')).toBeInTheDocument()
    expect(screen.getByText('Unreachable')).toBeInTheDocument()
    expect(screen.getByText('Disabled')).toBeInTheDocument()
    expect(screen.getByText(/connection refused or host unreachable/)).toBeInTheDocument()
    expect(screen.getByText(/Tools are hidden from everyone/)).toBeInTheDocument()
    expect(screen.getByText(`${window.location.origin}/mcp`)).toBeInTheDocument()
  })

  it('lists the prefixed tool names when expanded', async () => {
    const user = userEvent.setup()
    await openServers()
    const row = (await screen.findByText('WooCommerce')).closest('li')
    await user.click(within(row).getByRole('button', { name: 'Show tools' }))
    expect(screen.getByText('woocommerce__list_orders')).toBeInTheDocument()
  })

  it('adds a server, suggesting the short name and sending the credential only when given', async () => {
    const user = userEvent.setup()
    const calls = await openServers({ 'POST /api/servers': [201, SERVERS[0]] })
    await user.click(await screen.findByRole('button', { name: 'Add server' }))
    await user.type(screen.getByLabelText('Name'), 'My Search Tool')
    expect(screen.getByLabelText('Short name for tools')).toHaveValue('my-search-tool')
    await user.type(screen.getByLabelText('Server URL'), 'http://search.apps.svc:8000/mcp')
    await user.selectOptions(screen.getByLabelText('How the gateway signs in'), 'bearer')
    await user.type(screen.getByLabelText('Token'), 's3cret-token')
    await user.click(within(screen.getByRole('dialog', { name: 'Add a server' })).getByRole('button', { name: 'Add server' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/servers')).toBe(true))
    const post = calls.find((c) => c.method === 'POST' && c.url === '/api/servers')
    expect(post.body).toEqual({ name: 'My Search Tool', slug: 'my-search-tool', url: 'http://search.apps.svc:8000/mcp', auth_type: 'bearer', credential: 's3cret-token' })
  })

  it('never puts a stored credential back in the edit form', async () => {
    const user = userEvent.setup()
    await openServers()
    const row = (await screen.findByText('Notes')).closest('li')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    expect(screen.getByLabelText('Token')).toHaveValue('')
    expect(screen.getByText('Leave blank to keep the stored one.')).toBeInTheDocument()
  })

  it('asks before removing, naming the consequence', async () => {
    const user = userEvent.setup()
    const calls = await openServers({ 'DELETE /api/servers/s2': [204, null] })
    const row = (await screen.findByText('Notes')).closest('li')
    await user.click(within(row).getByRole('button', { name: 'Remove' }))
    expect(screen.getByRole('dialog', { name: 'Remove Notes?' })).toHaveTextContent('will disappear for everyone')
    await user.click(screen.getByRole('button', { name: 'Remove server' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/servers/s2')).toBe(true))
  })

  it('disables a server with one click', async () => {
    const user = userEvent.setup()
    const calls = await openServers({ 'PATCH /api/servers/s1': [200, SERVERS[0]] })
    const row = (await screen.findByText('WooCommerce')).closest('li')
    await user.click(within(row).getByRole('button', { name: 'Disable' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ enabled: false }))
  })
})

describe('team', () => {
  it('shows the temporary password and key once, then forgets them', async () => {
    const user = userEvent.setup()
    fakeGateway({
      'GET /api/me': [200, ADMIN],
      'GET /api/users': [200, [ADMIN]],
      'POST /api/users': [201, { user: { ...MEMBER }, key: { api_key: 'urm_ab12cd34_SECRETKEYVALUE' }, temporary_password: 'Temp-Pass-123' }],
    })
    window.location.hash = '#/team'
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Invite member' }))
    await user.type(screen.getByLabelText('Email'), 'bob@example.com')
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    const dialog = await screen.findByRole('dialog', { name: 'bob@example.com is invited' })
    expect(within(dialog).getByText('Temp-Pass-123')).toBeInTheDocument()
    expect(within(dialog).getByText('urm_ab12cd34_SECRETKEYVALUE')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'I have copied them' }))
    expect(screen.queryByText('Temp-Pass-123')).toBeNull()
    expect(screen.queryByText('urm_ab12cd34_SECRETKEYVALUE')).toBeNull()
  })
})

describe('audit log', () => {
  const ROWS = [
    { id: 2, ts: new Date().toISOString(), user_email: 'bob@example.com', server: 'alpha', tool: 'slow', requested_name: 'alpha__slow', duration_ms: 2001, outcome: 'timeout', detail: "'alpha' did not answer within 2s" },
    { id: 1, ts: new Date().toISOString(), user_email: 'admin@example.com', server: 'woocommerce', tool: 'list_orders', requested_name: 'woocommerce__list_orders', duration_ms: 84, outcome: 'success', detail: null },
  ]

  it('shows who did what, and filters by result', async () => {
    const user = userEvent.setup()
    fakeGateway({ 'GET /api/me': [200, ADMIN], 'GET /api/audit?limit=200': [200, ROWS] })
    window.location.hash = '#/activity'
    render(<App />)
    expect(await screen.findByText('woocommerce__list_orders')).toBeInTheDocument()
    expect(screen.getByText('84 ms')).toBeInTheDocument()
    expect(screen.getByText('2.0 s')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Result'), 'timeout')
    expect(screen.queryByText('woocommerce__list_orders')).toBeNull()
    expect(screen.getByText('alpha__slow')).toBeInTheDocument()
    expect(screen.getByText(/did not answer within 2s/)).toBeInTheDocument()
  })

  it('members see only their own activity, without a Who column', async () => {
    fakeGateway({ 'GET /api/me': [200, MEMBER], 'GET /api/me/audit?limit=200': [200, [ROWS[0]]] })
    window.location.hash = '#/activity'
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'My activity' })).toBeInTheDocument()
    expect(await screen.findByText('alpha__slow')).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Who' })).toBeNull()
  })
})

describe('api keys page', () => {
  it('gives copy-ready connection steps with a placeholder, and reveals a new key once', async () => {
    const user = userEvent.setup()
    fakeGateway({
      'GET /api/me': [200, MEMBER],
      'GET /api/me/keys': [200, []],
      'POST /api/me/keys': [201, { id: 'k1', prefix: 'ab12cd34', api_key: 'urm_ab12cd34_BRANDNEWKEY' }],
    })
    window.location.hash = '#/keys'
    render(<App />)
    expect(await screen.findByText(/claude mcp add --transport http urumi .*\/mcp --header "Authorization: Bearer YOUR_API_KEY"/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Name for the new key'), 'laptop')
    await user.click(screen.getByRole('button', { name: 'Create key' }))
    expect(await screen.findByText('urm_ab12cd34_BRANDNEWKEY')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'I have copied it' }))
    expect(screen.queryByText('urm_ab12cd34_BRANDNEWKEY')).toBeNull()
  })
})

describe('chat', () => {
  const REPLY = {
    reply: 'One order came in today: #15 for 80.00.',
    steps: 2, model: 'test-model',
    tool_calls: [{ name: 'woocommerce__list_orders', arguments: { period: 'today' }, outcome: 'success', duration_ms: 84, preview: '{"id": 15}' }],
  }

  async function openChat(user, extra = {}, me = MEMBER, status = { configured: true, model: 'test-model' }) {
    const calls = fakeGateway({ 'GET /api/me': [200, me], 'GET /api/chat/status': [200, status], ...extra })
    render(<App />)
    await screen.findByRole('heading', { name: 'Chat' })
    return calls
  }

  it('answers a starter question and shows which tools were used', async () => {
    const user = userEvent.setup()
    const calls = await openChat(user, { 'POST /api/chat': [200, REPLY] })
    await user.click(await screen.findByRole('button', { name: 'What orders came in today?' }))
    expect(await screen.findByText('One order came in today: #15 for 80.00.')).toBeInTheDocument()
    expect(screen.getByText('Used 1 tool')).toBeInTheDocument()
    expect(screen.getByText('woocommerce__list_orders')).toBeInTheDocument()
    expect(screen.getByText('Succeeded')).toBeInTheDocument()
    expect(screen.getByText('84 ms')).toBeInTheDocument()
    const post = calls.find((c) => c.method === 'POST' && c.url === '/api/chat')
    expect(post.body).toEqual({ messages: [{ role: 'user', content: 'What orders came in today?' }] })
    expect(post.headers['X-Requested-With']).toBe('urumi-dashboard')
  })

  it('reveals arguments and the result on request', async () => {
    const user = userEvent.setup()
    await openChat(user, { 'POST /api/chat': [200, REPLY] })
    await user.type(await screen.findByLabelText('Your message'), 'orders today{Enter}')
    await user.click(await screen.findByRole('button', { name: 'Show details' }))
    expect(screen.getByText(/"period": "today"/)).toBeInTheDocument()
    expect(screen.getByText('{"id": 15}')).toBeInTheDocument()
  })

  it('sends the earlier messages with each new one, and starts fresh on New chat', async () => {
    const user = userEvent.setup()
    const calls = await openChat(user, { 'POST /api/chat': [200, REPLY] })
    const box = await screen.findByLabelText('Your message')
    await user.type(box, 'first question{Enter}')
    await screen.findByText('One order came in today: #15 for 80.00.')
    await user.type(screen.getByLabelText('Your message'), 'and yesterday?{Enter}')
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST' && c.url === '/api/chat')).toHaveLength(2))
    const second = calls.filter((c) => c.method === 'POST' && c.url === '/api/chat')[1]
    expect(second.body.messages.map((m) => m.role)).toEqual(['user', 'assistant', 'user'])
    expect(second.body.messages.every((m) => Object.keys(m).sort().join() === 'content,role')).toBe(true)
    await user.click(screen.getByRole('button', { name: 'New chat' }))
    expect(screen.queryByText('first question')).toBeNull()
  })

  it('shows the service error and keeps the conversation', async () => {
    const user = userEvent.setup()
    await openChat(user, { 'POST /api/chat': [502, { detail: 'The AI service rejected the API key. An admin needs to update it (scripts/llm-secret.sh).' }] })
    await user.type(await screen.findByLabelText('Your message'), 'hello{Enter}')
    expect(await screen.findByRole('alert')).toHaveTextContent('rejected the API key')
    expect(screen.getByText('hello')).toBeInTheDocument()
  })

  it('tells an admin how to set it up, and a member to ask an admin', async () => {
    window.location.hash = '#/chat'
    await openChat(null, {}, ADMIN, { configured: false, model: null })
    expect(await screen.findByText(/scripts\/llm-secret\.sh/)).toBeInTheDocument()
    expect(screen.getByLabelText('Your message')).toBeDisabled()
    cleanup()
    window.location.hash = '#/chat'
    await openChat(null, {}, MEMBER, { configured: false, model: null })
    expect(await screen.findByText(/Ask an admin to add an AI service key/)).toBeInTheDocument()
  })

  it('marks calls made through the chat in the audit log', async () => {
    fakeGateway({
      'GET /api/me': [200, ADMIN],
      'GET /api/audit?limit=200': [200, [{ id: 1, ts: new Date().toISOString(), user_email: 'bob@example.com', server: 'woocommerce', tool: 'list_orders', requested_name: 'woocommerce__list_orders', duration_ms: 40, outcome: 'success', detail: null, source: 'chat' }]],
    })
    window.location.hash = '#/activity'
    render(<App />)
    expect(await screen.findByText('via chat')).toBeInTheDocument()
  })
})
