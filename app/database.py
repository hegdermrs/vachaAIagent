"""Database engine, session factory, and table creation."""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    """Create all tables. Call on startup."""
    from app.models import opportunity, artist, settings_models  # noqa: F401 — registers models
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)

    # Seed default artist profile and settings if empty
    async with async_session() as session:
        await _seed_defaults(session)


async def _migrate(conn):
    """Add columns introduced after the initial release (no Alembic here)."""
    from sqlalchemy import text, inspect

    new_columns = {
        "opportunities": [
            ("fee", "VARCHAR"),
            ("medium", "VARCHAR"),
            ("ai_summary", "TEXT"),
            ("ai_reasoning", "TEXT"),
        ],
    }
    for table, columns in new_columns.items():
        # Use SQLAlchemy inspector (works across SQLite + PostgreSQL)
        insp = await conn.run_sync(lambda sync_conn: inspect(sync_conn))
        existing = {col["name"] for col in insp.get_columns(table)}
        for col, ddl in columns:
            if col not in existing:
                await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


async def _seed_defaults(session: AsyncSession):
    from app.models.artist import ArtistProfile
    from app.models.settings_models import Setting
    from app.config import DEFAULT_SETTINGS
    from sqlalchemy import select

    # Seed artist profile
    result = await session.execute(select(ArtistProfile).limit(1))
    if result.scalar_one_or_none() is None:
        profile = ArtistProfile(
            name="Chandrashekar K",
            bio=(
                "Chandrashekar K is a multidisciplinary visual artist whose practice spans "
                "painting, sculpture, installation, digital and new media art, video, and performance. "
                "Their work explores the intersections of identity, migration, displacement, and memory, "
                "often engaging with urban landscapes and the impact of technology on human experience. "
                "Themes of post-colonial discourse, feminist art, the body, and gender are central to their practice. "
                "Based in the UK, they have exhibited internationally."
            ),
            mediums='["painting","sculpture","installation","digital / new media","video","performance"]',
            themes='["identity","migration","displacement","urban landscapes","memory","technology","post-colonial","women","feminist art","body","gender"]',
            portfolio_url="",
            cv_text="",
            website="",
        )
        session.add(profile)

    # Seed settings
    existing = await session.execute(select(Setting.key))
    existing_keys = set(existing.scalars().all())

    for key, value in DEFAULT_SETTINGS.items():
        if key not in existing_keys:
            session.add(Setting(key=key, value=value))

    await session.commit()

    await _seed_monitored_urls(session)
    await _sync_env_settings(session)


# Known art-opportunity listing pages, seeded once on first run.
DEFAULT_MONITORED_URLS = [
    ("https://resartis.org/open-calls/", "Res Artis – Open Calls"),
    ("https://www.transartists.org/en/map", "TransArtists – Residencies"),
    ("https://curatorspace.com/opportunities", "CuratorSpace – Opportunities"),
    ("https://www.artsthread.com/opportunities/", "ArtsThread – Opportunities"),
]


async def _seed_monitored_urls(session: AsyncSession):
    """Seed default listing URLs once (guarded by a flag so user deletions stick)."""
    from app.models.settings_models import Setting, MonitoredUrl
    from sqlalchemy import select

    flag = await session.get(Setting, "default_urls_seeded")
    if flag is not None and flag.value == "true":
        return

    existing = set((await session.execute(select(MonitoredUrl.url))).scalars().all())
    for url, label in DEFAULT_MONITORED_URLS:
        if url not in existing:
            session.add(MonitoredUrl(url=url, label=label, is_active=1))

    if flag is not None:
        flag.value = "true"
    else:
        session.add(Setting(key="default_urls_seeded", value="true"))
    await session.commit()


async def _sync_env_settings(session: AsyncSession):
    """Load settings from environment variables (.env).

    A setting is (re)written from .env when its current value is blank OR is
    still the unedited placeholder from .env.example. Genuine dashboard edits
    are never overwritten. Sensitive values are encrypted at rest.
    """
    import os
    from dotenv import dotenv_values
    from app.models.settings_models import Setting
    from app.config import (
        ENV_BACKED_SETTINGS, SENSITIVE_SETTINGS, encrypt_value, BASE_DIR,
    )
    from sqlalchemy import select

    example = dotenv_values(BASE_DIR / ".env.example")
    rows = {s.key: s for s in (await session.execute(select(Setting))).scalars().all()}

    changed = False
    for key, env_var in ENV_BACKED_SETTINGS.items():
        env_val = os.getenv(env_var, "").strip()
        if not env_val:
            continue
        placeholder = (example.get(env_var) or "").strip()
        row = rows.get(key)
        current = (row.value or "").strip() if row is not None else ""
        # Keep a value only if it's been set to something other than the placeholder
        if current and current != placeholder:
            continue
        stored = encrypt_value(env_val) if key in SENSITIVE_SETTINGS else env_val
        if row is not None:
            row.value = stored
        else:
            session.add(Setting(key=key, value=stored))
        changed = True

    if changed:
        await session.commit()


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an async session."""
    async with async_session() as session:
        yield session
