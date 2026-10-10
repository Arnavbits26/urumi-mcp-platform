"""Login / session / CSRF tests with a real Postgres.   Run: python -m tests.test_dashboard_login"""
import asyncio
import os
import socket
import tempfile

import asyncpg
import httpx
import pgserver
import uvicorn
from cryptography.fernet import Fernet

from app.security import generate_api_key

CSRF = {"X-Requested-With": "urumi-dashboard"}


def check(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print("PASS", label, extra)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main():
    pg = pgserver.get_server(tempfile.mkdtemp())
    admin_key, _, _ = generate_api_key()
    os.environ.update({
        "DATABASE_URL": pg.get_uri(), "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "BOOTSTRAP_ADMIN_KEY": admin_key, "BOOTSTRAP_ADMIN_EMAIL": "admin@example.com",
        "BOOTSTRAP_ADMIN_PASSWORD": "correct-horse-battery", "REFRESH_INTERVAL_SECONDS": "60",
    })
    from app.config import Settings
    from app.main import create_asgi
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_asgi(Settings()), host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    base = f"http://127.0.0.1:{port}"

    async def login(email, password, client=None):
        c = client or httpx.AsyncClient(base_url=base, timeout=20)
        r = await c.post("/api/auth/login", json={"email": email, "password": password})
        return c, r

    # ---- login basics ---------------------------------------------------------------------
    _, r1 = await login("admin@example.com", "wrong-password")
    _, r2 = await login("nobody@example.com", "whatever-it-is")
    check("wrong password -> 401", r1.status_code == 401)
    check("unknown email gives the identical answer", r2.status_code == 401 and r1.json() == r2.json(), r1.json()["detail"])

    admin, r = await login("admin@example.com", "correct-horse-battery")
    cookie_header = r.headers.get("set-cookie", "")
    check("admin login works", r.status_code == 200 and r.json()["role"] == "admin")
    check("session cookie is HttpOnly + SameSite=lax", "HttpOnly" in cookie_header and "samesite=lax" in cookie_header.lower(), cookie_header[:60])
    check("cookie is the only place the token appears", "urumi_session" not in r.text)
    check("cookie authenticates GET /api/me", (await admin.get("/api/me")).json()["email"] == "admin@example.com")
    admin_cookie = admin.cookies.get("urumi_session")

    # ---- CSRF: changes need the custom header when authenticated by cookie ---------------------
    body = {"email": "bob@example.com", "name": "Bob", "role": "member"}
    r = await admin.post("/api/users", json=body)
    check("cookie + no CSRF header -> 403 (forged cross-site request)", r.status_code == 403, r.text[:60])
    r = await admin.post("/api/users", json=body, headers=CSRF)
    check("cookie + CSRF header -> invite works", r.status_code == 201)
    temp, bob_id = r.json()["temporary_password"], r.json()["user"]["id"]
    check("invite returns a one-time password and key", len(temp) >= 10 and r.json()["key"]["api_key"].startswith("urm_"))
    conn = await asyncpg.connect(pg.get_uri())
    stored = await conn.fetchval("SELECT password_hash FROM users WHERE email='bob@example.com'")
    check("password stored as a scrypt hash, not the password", stored.startswith("scrypt$") and temp not in stored)
    r = await httpx.AsyncClient(base_url=base).post("/api/users", json=body, headers={"Authorization": f"Bearer {admin_key}"})
    check("API key auth needs no CSRF header (and duplicate email -> 409)", r.status_code == 409)

    # ---- member session ---------------------------------------------------------------------
    bob, r = await login("bob@example.com", temp)
    check("member logs in with the temporary password", r.status_code == 200 and r.json()["role"] == "member")
    check("member cannot reach admin pages", (await bob.get("/api/servers")).status_code == 403)
    check("member sees own keys", (await bob.get("/api/me/keys")).status_code == 200)
    r = await bob.post("/api/me/keys", json={"name": "laptop"}, headers=CSRF)
    check("member can create their own API key from the dashboard", r.status_code == 201 and r.json()["api_key"].startswith("urm_"))

    # ---- change password --------------------------------------------------------------------
    r = await bob.post("/api/me/password", json={"current_password": "nope-nope-nope", "new_password": "a-brand-new-pass"}, headers=CSRF)
    check("change password: wrong current password -> 400", r.status_code == 400)
    r = await bob.post("/api/me/password", json={"current_password": temp, "new_password": "short"}, headers=CSRF)
    check("change password: too short -> 422", r.status_code == 422)
    r = await bob.post("/api/me/password", json={"current_password": temp, "new_password": "a-brand-new-pass"}, headers=CSRF)
    check("change password works", r.status_code == 204)
    check("old password stops working", (await login("bob@example.com", temp))[1].status_code == 401)
    bob2, r = await login("bob@example.com", "a-brand-new-pass")
    check("new password works", r.status_code == 200)

    # ---- logout, disable, reset ---------------------------------------------------------------
    bob_cookie = bob2.cookies.get("urumi_session")
    r = await bob2.post("/api/auth/logout", headers=CSRF)
    check("logout", r.status_code == 204)
    stale = httpx.AsyncClient(base_url=base, cookies={"urumi_session": bob_cookie})
    check("a copied session cookie is dead after logout", (await stale.get("/api/me")).status_code == 401)

    bob3, _ = await login("bob@example.com", "a-brand-new-pass")
    r = await admin.patch(f"/api/users/{bob_id}", json={"disabled": True}, headers=CSRF)
    check("admin disables a user", r.status_code == 200)
    check("disabled user's live session stops at once", (await bob3.get("/api/me")).status_code == 401)
    check("disabled user cannot log in", (await login("bob@example.com", "a-brand-new-pass"))[1].status_code == 401)
    await admin.patch(f"/api/users/{bob_id}", json={"disabled": False}, headers=CSRF)

    bob4, _ = await login("bob@example.com", "a-brand-new-pass")
    r = await admin.post(f"/api/users/{bob_id}/reset-password", headers=CSRF)
    new_temp = r.json()["temporary_password"]
    check("admin resets a password", r.status_code == 200 and new_temp != temp)
    check("reset signs the user out everywhere", (await bob4.get("/api/me")).status_code == 401)
    check("reset password works", (await login("bob@example.com", new_temp))[1].status_code == 200)
    check("admin cannot disable or demote themself", (await admin.patch(f"/api/users/{(await admin.get('/api/me')).json()['id']}", json={"disabled": True}, headers=CSRF)).status_code == 400)

    # ---- throttling ---------------------------------------------------------------------------
    codes = [(await login("victim@example.com", f"guess-number-{i}"))[1].status_code for i in range(10)]
    check("8 failures are allowed, then 429", codes[:8] == [401] * 8 and codes[8:] == [429, 429], str(codes))
    check("throttle is per email: others unaffected", (await login("admin@example.com", "correct-horse-battery"))[1].status_code == 200)

    # ---- the API-key path is untouched -----------------------------------------------------
    r = await httpx.AsyncClient(base_url=base).get("/api/me", headers={"Authorization": f"Bearer {admin_key}"})
    check("API keys still work", r.status_code == 200 and r.json()["role"] == "admin")
    check("no credentials -> 401", (await httpx.AsyncClient(base_url=base).get("/api/me")).status_code == 401)

    await conn.close()
    server.should_exit = True
    await task
    pg.cleanup()
    print("ALL LOGIN TESTS PASSED")


asyncio.run(main())
