"""
config/settings.py
==================
Single source of truth for all environment configuration.
Loaded once at startup. Accessed everywhere via get_settings().

Usage:
    from app.config.settings import get_settings
    settings = get_settings()
    print(settings.rpc_url)
"""

from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_name: str = "NOMO-WORKCHAIN Backend"
    app_version: str = "0.1.0"
    debug: bool = False
    environment: str = "development"  # development | staging | production

    # ── Monad / Blockchain ────────────────────────────────────────────────────
    rpc_url: str = "https://testnet-rpc.monad.xyz"
    chain_id: int = 10143
    verifier_private_key: str = ""   # Signs confirmProof() transactions
    owner_private_key: str = ""      # Admin calls: setRegistry, etc.

    # ── Contract Addresses (fill after deployment) ────────────────────────────
    workchain_address: str = ""
    registry_address: str = ""
    milestone_manager_address: str = ""
    dispute_resolver_address: str = ""

    # ── Database ──────────────────────────────────────────────────────────────
    # SQLite (dev):  sqlite+aiosqlite:///./workchain.db
    # Postgres (prod): postgresql+asyncpg://user:pass@localhost/workchain
    database_url: str = "sqlite+aiosqlite:///./workchain.db"

    # ── Redis / Celery ────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ── Verification ──────────────────────────────────────────────────────────
    github_token: Optional[str] = None
    ipfs_gateway: str = "https://ipfs.io/ipfs"
    verification_timeout_seconds: int = 300
    max_link_redirects: int = 5

    # ── Notifications (Telegram) ──────────────────────────────────────────────
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # ── Dispute ───────────────────────────────────────────────────────────────
    dispute_evidence_window_hours: int = 48
    arbitrator_wallet: str = ""

    # ── Security ──────────────────────────────────────────────────────────────
    secret_key: str = "change-me-in-production"
    allowed_origins: list[str] = ["http://localhost:3000"]

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    sentry_dsn: Optional[str] = None

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def verifier_address(self) -> str:
        if not self.verifier_private_key:
            return ""
        try:
            from eth_account import Account
            return Account.from_key(self.verifier_private_key).address
        except Exception:
            return ""


@lru_cache()
def get_settings() -> Settings:
    """Cached settings — .env read once at startup."""
    return Settings()
