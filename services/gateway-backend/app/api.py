"""Admin and self-service REST API used by the dashboard (and by scripts)."""
from __future__ import annotations

from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import AuthUser, authenticate, bearer_token
from .registry import public_name
from .security import generate_api_key, validate_header_name, validate_upstream_url

router = APIRouter(prefix="/api")
SLUG = r"^[a-z][a-z0-9-]{1,23}$"      # no underscore, so the "__" separator in tool names is unambiguous


def gw(request: Request):
    return request.app.state.gw


async def current_user(request: Request) -> AuthUser:
    user = await authenticate(gw(request).db, bearer_token(request.headers.get("authorization")))
    if user is None:
        raise HTTPException(401, "invalid, revoked or missing API key", headers={"WWW-Authenticate": "Bearer"})
    return user


async def admin_user(user: AuthUser = Depends(current_user)) -> AuthUser:
    if not user.is_admin:
        raise HTTPException(403, "admin role required")
    return user


# ---------------------------------------------------------------------------- servers
class ServerIn(BaseModel):
    slug: str = Field(pattern=SLUG)
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(max_length=500)
    auth_type: Literal["none", "bearer", "header"] = "none"
    auth_header_name: str | None = None
    credential: str | None = Field(default=None, max_length=4000)


class ServerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    url: str | None = Field(default=None, max_length=500)
    auth_type: Literal["none", "bearer", "header"] | None = None
    auth_header_name: str | None = None
    credential: str | None = Field(default=None, max_length=4000)   # "" clears it; omit to keep it
    enabled: bool | None = None


def server_out(row, registry) -> dict:
    up = registry.get(row["slug"])
    return {
        "id": str(row["id"]), "slug": row["slug"], "name": row["name"], "url": row["url"],
        "auth_type": row["auth_type"], "auth_header_name": row["auth_header_name"],
        "has_credential": bool(row["credential_enc"]),          # the credential itself is never returned
        "enabled": row["enabled"], "status": row["status"], "status_detail": row["status_detail"],
        "last_checked_at": row["last_checked_at"], "created_at": row["created_at"],
        "tools": [{"name": t.name, "public_name": public_name(row["slug"], t.name), "description": t.description}
                  for t in (up.tools if up else [])],
    }


async def _check_url(request: Request, url: str) -> None:
    try:
        await validate_upstream_url(url, gw(request).settings.allow_loopback_upstreams)
    except ValueError as exc:
        raise HTTPException(400, f"upstream URL rejected: {exc}") from exc


@router.get("/servers")
async def list_servers(request: Request, _: AuthUser = Depends(admin_user)):
    rows = await gw(request).db.fetch("SELECT * FROM mcp_servers ORDER BY created_at")
    return [server_out(r, gw(request).registry) for r in rows]


@router.post("/servers", status_code=201)
async def create_server(body: ServerIn, request: Request, _: AuthUser = Depends(admin_user)):
    g = gw(request)
    await _check_url(request, body.url)
    if body.auth_type == "header":
        try:
            validate_header_name(body.auth_header_name or "")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if body.auth_type != "none" and not body.credential:
        raise HTTPException(400, "a credential is required for this auth type")
    enc = g.box.encrypt(body.credential) if body.auth_type != "none" and body.credential else None
    try:
        row = await g.db.fetchrow(
            """INSERT INTO mcp_servers (slug, name, url, auth_type, auth_header_name, credential_enc)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING *""",
            body.slug, body.name, body.url, body.auth_type,
            body.auth_header_name if body.auth_type == "header" else None, enc)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(409, f"slug '{body.slug}' already exists") from exc
    await g.registry.refresh_one(body.slug)
    return server_out(await g.db.fetchrow("SELECT * FROM mcp_servers WHERE id=$1", row["id"]), g.registry)


@router.patch("/servers/{server_id}")
async def update_server(server_id: str, body: ServerPatch, request: Request, _: AuthUser = Depends(admin_user)):
    g = gw(request)
    row = await g.db.fetchrow("SELECT * FROM mcp_servers WHERE id=$1::uuid", server_id)
    if row is None:
        raise HTTPException(404, "server not found")
    sent = body.model_fields_set
    new = dict(row)
    if "name" in sent and body.name:
        new["name"] = body.name
    if "url" in sent and body.url:
        await _check_url(request, body.url)
        new["url"] = body.url
    if "auth_type" in sent and body.auth_type:
        new["auth_type"] = body.auth_type
    if "auth_header_name" in sent:
        new["auth_header_name"] = body.auth_header_name
    if "credential" in sent:
        new["credential_enc"] = g.box.encrypt(body.credential) if body.credential else None
    if new["auth_type"] == "none":
        new["credential_enc"], new["auth_header_name"] = None, None
    if new["auth_type"] == "header":
        try:
            validate_header_name(new["auth_header_name"] or "")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if new["auth_type"] != "none" and not new["credential_enc"]:
        raise HTTPException(400, "a credential is required for this auth type")
    if "enabled" in sent and body.enabled is not None:
        new["enabled"] = body.enabled
    await g.db.execute(
        """UPDATE mcp_servers SET name=$2, url=$3, auth_type=$4, auth_header_name=$5, credential_enc=$6, enabled=$7
           WHERE id=$1::uuid""",
        server_id, new["name"], new["url"], new["auth_type"], new["auth_header_name"], new["credential_enc"], new["enabled"])
    await g.registry.refresh_one(row["slug"])
    return server_out(await g.db.fetchrow("SELECT * FROM mcp_servers WHERE id=$1::uuid", server_id), g.registry)


@router.delete("/servers/{server_id}", status_code=204)
async def delete_server(server_id: str, request: Request, _: AuthUser = Depends(admin_user)):
    g = gw(request)
    row = await g.db.fetchrow("DELETE FROM mcp_servers WHERE id=$1::uuid RETURNING slug", server_id)
    if row is None:
        raise HTTPException(404, "server not found")
    g.registry.forget(row["slug"])


@router.post("/servers/{server_id}/refresh")
async def refresh_server(server_id: str, request: Request, _: AuthUser = Depends(admin_user)):
    g = gw(request)
    row = await g.db.fetchrow("SELECT slug FROM mcp_servers WHERE id=$1::uuid", server_id)
    if row is None:
        raise HTTPException(404, "server not found")
    await g.registry.refresh_one(row["slug"])
    return server_out(await g.db.fetchrow("SELECT * FROM mcp_servers WHERE id=$1::uuid", server_id), g.registry)


# ---------------------------------------------------------------------------- users and keys
class UserIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    name: str = Field(default="", max_length=100)
    role: Literal["admin", "member"] = "member"


class UserPatch(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    role: Literal["admin", "member"] | None = None
    disabled: bool | None = None


class KeyIn(BaseModel):
    name: str = Field(default="", max_length=80)


def user_out(r) -> dict:
    return {"id": str(r["id"]), "email": r["email"], "name": r["name"], "role": r["role"],
            "disabled": r["disabled"], "created_at": r["created_at"]}


def key_out(r) -> dict:
    return {"id": str(r["id"]), "name": r["name"], "prefix": r["prefix"], "created_at": r["created_at"],
            "last_used_at": r["last_used_at"], "revoked_at": r["revoked_at"]}


async def _new_key(g, user_id: str, name: str) -> dict:
    full, prefix, digest = generate_api_key()
    row = await g.db.fetchrow(
        "INSERT INTO api_keys (user_id, name, prefix, key_hash) VALUES ($1::uuid,$2,$3,$4) RETURNING *",
        user_id, name, prefix, digest)
    return {**key_out(row), "api_key": full}   # the full key is shown exactly once


@router.get("/me")
async def me(user: AuthUser = Depends(current_user)):
    return {"id": user.id, "email": user.email, "role": user.role}


@router.get("/users")
async def list_users(request: Request, _: AuthUser = Depends(admin_user)):
    return [user_out(r) for r in await gw(request).db.fetch("SELECT * FROM users ORDER BY created_at")]


@router.post("/users", status_code=201)
async def invite_user(body: UserIn, request: Request, _: AuthUser = Depends(admin_user)):
    g = gw(request)
    try:
        row = await g.db.fetchrow("INSERT INTO users (email, name, role) VALUES ($1,$2,$3) RETURNING *",
                                  body.email.lower(), body.name, body.role)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(409, "a user with this email already exists") from exc
    return {"user": user_out(row), "key": await _new_key(g, str(row["id"]), "first key")}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserPatch, request: Request, admin: AuthUser = Depends(admin_user)):
    g = gw(request)
    row = await g.db.fetchrow("SELECT * FROM users WHERE id=$1::uuid", user_id)
    if row is None:
        raise HTTPException(404, "user not found")
    role = body.role or row["role"]
    disabled = row["disabled"] if body.disabled is None else body.disabled
    if user_id == admin.id and (role != "admin" or disabled):
        raise HTTPException(400, "you cannot demote or disable yourself")
    name = row["name"] if body.name is None else body.name
    row = await g.db.fetchrow("UPDATE users SET name=$2, role=$3, disabled=$4 WHERE id=$1::uuid RETURNING *",
                              user_id, name, role, disabled)
    return user_out(row)


@router.post("/users/{user_id}/keys", status_code=201)
async def issue_key(user_id: str, body: KeyIn, request: Request, _: AuthUser = Depends(admin_user)):
    g = gw(request)
    if await g.db.fetchval("SELECT 1 FROM users WHERE id=$1::uuid", user_id) is None:
        raise HTTPException(404, "user not found")
    return await _new_key(g, user_id, body.name)


@router.get("/users/{user_id}/keys")
async def user_keys(user_id: str, request: Request, _: AuthUser = Depends(admin_user)):
    rows = await gw(request).db.fetch("SELECT * FROM api_keys WHERE user_id=$1::uuid ORDER BY created_at DESC", user_id)
    return [key_out(r) for r in rows]


@router.get("/me/keys")
async def my_keys(request: Request, user: AuthUser = Depends(current_user)):
    rows = await gw(request).db.fetch("SELECT * FROM api_keys WHERE user_id=$1::uuid ORDER BY created_at DESC", user.id)
    return [key_out(r) for r in rows]


@router.post("/me/keys", status_code=201)
async def create_my_key(body: KeyIn, request: Request, user: AuthUser = Depends(current_user)):
    return await _new_key(gw(request), user.id, body.name)


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_key(key_id: str, request: Request, user: AuthUser = Depends(current_user)):
    g = gw(request)
    row = await g.db.fetchrow("SELECT user_id FROM api_keys WHERE id=$1::uuid", key_id)
    if row is None or (str(row["user_id"]) != user.id and not user.is_admin):
        raise HTTPException(404, "key not found")          # 404, not 403: do not reveal other users' key ids
    await g.db.execute("UPDATE api_keys SET revoked_at = now() WHERE id=$1::uuid AND revoked_at IS NULL", key_id)


# ---------------------------------------------------------------------------- tools and audit
@router.get("/tools")
async def tools(request: Request, _: AuthUser = Depends(current_user)):
    return [{"name": public_name(up.slug, t.name), "server": up.slug, "description": t.description}
            for up, t in gw(request).registry.listed_tools()]


def audit_out(r) -> dict:
    return {"id": r["id"], "ts": r["ts"], "user_email": r["user_email"], "server": r["server_slug"],
            "tool": r["tool_name"], "requested_name": r["requested_name"], "duration_ms": r["duration_ms"],
            "outcome": r["outcome"], "detail": r["detail"]}


@router.get("/audit")
async def audit(request: Request, _: AuthUser = Depends(admin_user), limit: int = Query(100, ge=1, le=500),
                user_email: str | None = None, server: str | None = None, outcome: str | None = None):
    rows = await gw(request).db.fetch(
        """SELECT * FROM audit_log WHERE ($1::text IS NULL OR user_email=$1) AND ($2::text IS NULL OR server_slug=$2)
           AND ($3::text IS NULL OR outcome=$3) ORDER BY id DESC LIMIT $4""", user_email, server, outcome, limit)
    return [audit_out(r) for r in rows]


@router.get("/me/audit")
async def my_audit(request: Request, user: AuthUser = Depends(current_user), limit: int = Query(100, ge=1, le=500)):
    rows = await gw(request).db.fetch("SELECT * FROM audit_log WHERE user_id=$1::uuid ORDER BY id DESC LIMIT $2", user.id, limit)
    return [audit_out(r) for r in rows]
