# 0004 - Dashboard and login

**One address.** The ingress sends `/api` and `/mcp` to the gateway and everything else to the
dashboard, all on `gateway.localtest.me`. The browser sees a single origin, so there is no CORS, the
cookie is first-party, and MCP clients and people use the same host.

**Why sessions, not JWT.** A dashboard session is a random 256-bit token in an `HttpOnly`,
`SameSite=Lax` cookie (and `Secure` over https). Only its SHA-256 is stored. The database is checked on
every request, so logout, disabling a user and resetting a password take effect immediately, which
stateless JWTs cannot do without extra machinery.

**CSRF.** Browsers attach cookies automatically, so a request forged from another website would carry
the session. Every change made with a cookie must also carry `X-Requested-With: urumi-dashboard`;
other sites cannot add a custom header without a CORS preflight, which the gateway never grants.
API-key requests use `Authorization: Bearer` (not attached automatically) and need no such header.

**Passwords.** scrypt (slow and memory-hungry) with a per-password salt. Passwords are human-chosen
and low-entropy, unlike API keys, which is why the two use different hashes. Login always runs one
hash check, even for unknown emails, so response time does not reveal which emails exist, and the error
text is identical. Eight failed logins per email and address in ten minutes returns 429. This counter is
in memory per replica; with several replicas the limit multiplies, and a shared store would fix that.

**Onboarding without email.** Inviting someone creates the account, a one-time temporary password and a
first API key, shown once to the admin to hand over. Resetting a password signs the person out
everywhere. The first admin's password comes from the Kubernetes Secret and is only applied while the
admin has none.

**Strict content security policy.** The app uses no inline scripts or styles and bundles its fonts, so
nginx sends `default-src 'self'`, no framing, and `nosniff`. `/.well-known/` answers 404, because MCP
clients probe it for OAuth after a 401 and must not receive the app's HTML page.

**Role checks stay on the server.** The dashboard hides pages a member cannot use, but the backend
enforces roles on every endpoint; hiding is only convenience.
