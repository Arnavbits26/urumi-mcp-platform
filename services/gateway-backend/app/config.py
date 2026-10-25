"""Configuration from environment variables. Secrets arrive via Kubernetes Secrets."""
from __future__ import annotations

import json
import os
from urllib.parse import quote


def _req(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


class Settings:
    def __init__(self) -> None:
        url = os.environ.get("DATABASE_URL", "").strip()
        if not url:
            url = "postgresql://{u}:{p}@{h}:{port}/{d}".format(
                u=quote(os.environ.get("DB_USER", "gateway")),
                p=quote(_req("DB_PASSWORD")),
                h=os.environ.get("DB_HOST", "postgres"),
                port=os.environ.get("DB_PORT", "5432"),
                d=os.environ.get("DB_NAME", "gateway"),
            )
        self.database_url = url
        self.encryption_key = _req("ENCRYPTION_KEY")            # Fernet key, encrypts upstream credentials
        self.bootstrap_admin_key = os.environ.get("BOOTSTRAP_ADMIN_KEY", "").strip()
        self.bootstrap_admin_password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
        self.cookie_secure = _bool("COOKIE_SECURE", False)       # true when the dashboard is served over https
        self.session_hours = int(os.environ.get("SESSION_HOURS", "12"))
        self.bootstrap_admin_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
        self.seed_servers: list[dict] = json.loads(os.environ.get("SEED_SERVERS", "[]") or "[]")
        self.call_timeout = float(os.environ.get("CALL_TIMEOUT_SECONDS", "30"))
        self.connect_timeout = float(os.environ.get("CONNECT_TIMEOUT_SECONDS", "5"))
        self.refresh_timeout = float(os.environ.get("REFRESH_TIMEOUT_SECONDS", "10"))
        self.refresh_interval = float(os.environ.get("REFRESH_INTERVAL_SECONDS", "15"))
        self.fail_threshold = int(os.environ.get("FAIL_THRESHOLD", "2"))
        self.allow_loopback_upstreams = _bool("UPSTREAM_ALLOW_LOOPBACK", False)  # tests / local dev only
        # Chat assistant: any OpenAI-compatible service. The key comes from a Kubernetes Secret.
        self.llm_api_key = os.environ.get("LLM_API_KEY", "").strip()
        self.llm_base_url = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1").strip()
        self.llm_model = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile").strip()
        self.llm_timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
        self.chat_max_steps = int(os.environ.get("CHAT_MAX_STEPS", "6"))
        self.chat_timeout = float(os.environ.get("CHAT_TIMEOUT_SECONDS", "120"))
        # How the chat hands tools to the model: "all" of them, "compact" (search first), or "auto" (compact above N tools).
        self.chat_tool_mode = os.environ.get("CHAT_TOOL_MODE", "auto").strip().lower()
        if self.chat_tool_mode not in ("auto", "all", "compact"):
            raise RuntimeError("CHAT_TOOL_MODE must be auto, all or compact")
        self.chat_compact_above = int(os.environ.get("CHAT_COMPACT_ABOVE", "30"))
        self.chat_rate_per_minute = int(os.environ.get("CHAT_RATE_PER_MINUTE", "12"))
        # Requests per API key per window on /mcp. 0 turns the limit off. The window is only changed in tests.
        self.mcp_rate_limit_per_minute = int(os.environ.get("MCP_RATE_LIMIT_PER_MINUTE", "120"))
        self.mcp_rate_window_seconds = float(os.environ.get("MCP_RATE_WINDOW_SECONDS", "60"))
        # Where request counts live. "memory" is right for one replica; with several, use "postgres" so they share one count.
        self.rate_limit_store = os.environ.get("RATE_LIMIT_STORE", "memory").strip().lower()
        if self.rate_limit_store not in ("memory", "postgres"):
            raise RuntimeError("RATE_LIMIT_STORE must be memory or postgres")
        self.served_by_header = _bool("SERVED_BY_HEADER", False)      # adds X-Served-By: <pod name>, to show load balancing
        self.instance_name = os.environ.get("HOSTNAME", "gateway")
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8080"))
