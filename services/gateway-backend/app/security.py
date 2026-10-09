"""API keys, credential encryption and upstream URL validation."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import re
import secrets
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

KEY_PREFIX = "urm"


# ---- API keys -----------------------------------------------------------------------------
# Format: urm_<8 hex>_<random secret>. The 8-hex part is a public lookup id. Only the SHA-256 of the
# whole key is stored. SHA-256 (not bcrypt) is right here: the key is 256 bits of randomness, so
# there is nothing to brute-force, and we need to check it on every request.

def generate_api_key() -> tuple[str, str, str]:
    prefix = secrets.token_hex(4)
    full = f"{KEY_PREFIX}_{prefix}_{secrets.token_urlsafe(32)}"
    return full, prefix, hash_key(full)


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()


def parse_key_prefix(token: str) -> str | None:
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX or not re.fullmatch(r"[0-9a-f]{8}", parts[1]) or not parts[2]:
        return None
    return parts[1]


def key_matches(token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_key(token), stored_hash)


# ---- Credentials at rest ------------------------------------------------------------------

class CredentialBox:
    """Encrypts upstream credentials before they are written to the database."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("stored credential cannot be decrypted (wrong ENCRYPTION_KEY?)") from exc


# ---- Upstream URL validation (SSRF guard) -------------------------------------------------

_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_FORBIDDEN_HEADERS = {"host", "content-length", "content-type", "transfer-encoding", "connection"}


def validate_header_name(name: str) -> str:
    if not _HEADER_NAME.fullmatch(name) or name.lower() in _FORBIDDEN_HEADERS:
        raise ValueError("invalid header name")
    return name


async def validate_upstream_url(url: str, allow_loopback: bool = False) -> None:
    """Reject URLs that could be used to reach things an admin should not point the gateway at.

    Blocked always: non-http(s) schemes, credentials inside the URL, link-local addresses (this
    includes the cloud metadata endpoint 169.254.169.254), unspecified and multicast addresses.
    Blocked unless allowed for development: loopback. Private ranges (10.x etc.) stay allowed
    because the real upstreams are cluster-internal; a host allow-list is a planned hardening step.
    Every address a name resolves to is checked, and the check is repeated before each connection.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL must be http(s) with a host")
    if parsed.username or parsed.password:
        raise ValueError("credentials inside the URL are not allowed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, port, type=0)
    except OSError as exc:
        raise ValueError("host name cannot be resolved") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0].split("%")[0])
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        if addr.is_loopback and not allow_loopback:
            raise ValueError("loopback addresses are not allowed")
        if addr.is_link_local or addr.is_unspecified or addr.is_multicast or addr.is_reserved:
            raise ValueError("address range is not allowed (link-local, metadata, multicast or reserved)")
