"""Async SQLAlchemy engine and session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.infra.cache.transaction_cache import invalidate_after_commit
from app.infra.database.models import Base

_engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=settings.DEBUG,
)

AsyncSessionFactory = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def init_db() -> None:
    """Create all tables defined in the ORM models if they do not yet exist.

    Called once during application startup via the FastAPI lifespan handler.
    For production deployments, prefer Alembic migrations over this function.
    """
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:  # type: ignore[return]
    """FastAPI dependency that yields an async database session per request.

    The session is automatically committed on success and rolled back on
    any exception, then closed regardless of outcome.

    Yields:
        An active :class:`AsyncSession` bound to the shared engine pool.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
            # Invalidate only after the PostgreSQL commit succeeds. A rollback
            # must not evict a valid cache entry or expose uncommitted data.
            await invalidate_after_commit(
                session.info.pop("transaction_cache_invalidate", set())
            )
        except Exception:
            await session.rollback()
            raise
