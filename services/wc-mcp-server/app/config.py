"""Configuration, read from environment variables (injected from a Kubernetes Secret)."""
import os


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class Settings:
    def __init__(self) -> None:
        # In-cluster URL of the WordPress Service, e.g. http://wordpress
        self.base_url = _required("WC_BASE_URL").rstrip("/")
        self.consumer_key = _required("WC_CONSUMER_KEY")
        self.consumer_secret = _required("WC_CONSUMER_SECRET")
        self.timeout = float(os.environ.get("WC_TIMEOUT_SECONDS", "15"))
        self.host = os.environ.get("MCP_HOST", "0.0.0.0")
        self.port = int(os.environ.get("MCP_PORT", "8000"))
        # Optional: comma-separated Host headers to accept (DNS-rebinding protection).
        self.allowed_hosts = [
            h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()
        ]
