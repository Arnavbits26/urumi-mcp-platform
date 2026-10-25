// All calls go to /api on the same origin. The session cookie is HttpOnly, so this code never sees it.
export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

let unauthorizedHandler = () => {}
export function onUnauthorized(fn) {
  unauthorizedHandler = fn
}

function messageFrom(data, status) {
  if (typeof data?.detail === 'string') return data.detail
  if (Array.isArray(data?.detail)) return data.detail.map((d) => d.msg).join('; ')
  return `Request failed (HTTP ${status}).`
}

async function request(method, path, body) {
  const headers = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  // Changes carry a custom header; the gateway rejects cookie-authenticated changes without it
  // (protection against forged requests from other websites).
  if (method !== 'GET') headers['X-Requested-With'] = 'urumi-dashboard'
  let res
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Cannot reach the gateway. Check your connection and try again.')
  }
  if (res.status === 204) return null
  let data = null
  try {
    data = await res.json()
  } catch {
    /* the body was not JSON */
  }
  if (!res.ok) {
    if (res.status === 401 && path !== '/auth/login') unauthorizedHandler()
    throw new ApiError(res.status, messageFrom(data, res.status))
  }
  return data
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body = {}) => request('POST', path, body),
  put: (path, body) => request('PUT', path, body),
  patch: (path, body) => request('PATCH', path, body),
  del: (path) => request('DELETE', path),
}
