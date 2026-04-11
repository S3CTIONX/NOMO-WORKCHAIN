"""
db/session.py
=============
Database engine and session factory.
Supports SQLite (dev) and PostgreSQL (prod) via DATABASE_URL in .env.

Usage:
    from app.db.session import get_db

    @app.get("/example")
    async def example(db: AsyncSession = Depends(get_db)):
        result = await db.execute(select(MyModel))
        return result.scalars().all()
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy import create_engine

from app.config.settings import get_settings

settings = get_settings()

# ── Async Engine (for FastAPI routes) ─────────────────────────────────────────
connect_args = {"check_same_thread": False} if "sqlite" in settings.database_url else {}

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
    connect_args=connect_args,
)

# ── Async Session factory ─────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ── Sync Engine + SessionLocal (for models.py helper functions) ───────────────
# Convert async URL to sync: sqlite+aiosqlite → sqlite, postgresql+asyncpg → postgresql
_sync_url = settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
_sync_connect_args = {"check_same_thread": False} if "sqlite" in _sync_url else {}

sync_engine = create_engine(
    _sync_url,
    echo=settings.debug,
    connect_args=_sync_connect_args,
)

SessionLocal = sessionmaker(
    bind=sync_engine,
    autoflush=False,
    autocommit=False,
)

# ── Base class for all models ─────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass



# ── Dependency for FastAPI routes ─────────────────────────────────────────────
async def get_db() -> AsyncSession:
    """
    FastAPI dependency. Yields a database session per request.
    Commits on success, rolls back on exception, always closes.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Create all tables (called from main.py on startup) ───────────────────────
async def create_tables():
    """Create all tables defined in models.py. Safe to call repeatedly."""
    from app.db import models  # noqa: F401 — import triggers model registration
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
