"""Application settings (environment-driven)."""

from __future__ import annotations

import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TAB_", env_file=".env", extra="ignore")

    app_name: str = "TabLedger"
    version: str = "0.1.0"
    data_dir: Path = Path("/data")
    # Secret key for authenticated encryption of stored credentials.
    # If unset, one is generated and persisted under data_dir/secrets/.
    secret_key: str = ""
    # Bootstrap admin (first start only; the plaintext is never stored).
    bootstrap_admin: str = "admin"
    bootstrap_password: str = ""
    # Cookie name. __Host- prefix requires HTTPS; dev override drops the prefix.
    cookie_name: str = "__Host-tab_session"
    csrf_cookie_name: str = "tab_csrf"
    insecure_cookies: bool = False
    session_ttl_hours: int = 24 * 7
    # Comma-separated allowed hosts for TrustedHost validation ("*" to allow any, dev only).
    allowed_hosts: str = "*"
    # Behind the local Caddy reverse proxy only: trust X-Forwarded-For from these proxies.
    trusted_proxies: str = "127.0.0.1,::1"
    # Upload limits
    max_upload_mb: int = 100
    max_request_mb: int = 200
    # AI defaults
    ai_default_timeout: int = 60
    log_level: str = "INFO"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "tabledger.sqlite3"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    def ensure_secret_key(self) -> str:
        """Return the persistent secret key, generating one on first start."""

        if self.secret_key:
            return self.secret_key
        self.secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_file = self.secrets_dir / "app.key"
        if key_file.exists():
            return key_file.read_text().strip()
        generated = secrets.token_urlsafe(48)
        key_file.write_text(generated)
        key_file.chmod(0o600)
        return generated


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
