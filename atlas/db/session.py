"""Engine/session factory and schema bootstrap."""
from __future__ import annotations

import json

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from atlas import config
from atlas.db.models import Base, Source

config.ensure_dirs()
engine = create_engine(config.DB_URL, future=True, connect_args={"timeout": 60})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=60000")
    cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


_SCHEMA_READY = False


def get_session() -> Session:
    global _SCHEMA_READY
    if not _SCHEMA_READY:
        _SCHEMA_READY = True
        init_db()          # idempotent: creates tables / adds new columns
    return SessionLocal()


FTS_SQL = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS scholarships_fts USING fts5("
    "name, provider, eligibility_text, content='scholarships', content_rowid='id')",
]


def _migrate_add_columns(conn) -> None:
    """Additive migrations: add any model column missing from an existing SQLite table."""
    for table in Base.metadata.sorted_tables:
        existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table.name})"))}
        if not existing:
            continue
        for col in table.columns:
            if col.name not in existing:
                ddl_type = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type}"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        _migrate_add_columns(conn)
        for stmt in FTS_SQL:
            conn.execute(text(stmt))


def checkpoint() -> None:
    """Fold the WAL into the main file and switch to a single-file journal, so data/atlas.db can be
    committed to git / read by the dashboard without its -wal/-shm side files."""
    import logging

    from sqlalchemy.exc import OperationalError
    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            conn.execute(text("PRAGMA journal_mode=DELETE"))
            conn.commit()
    except OperationalError as e:
        # another process (e.g. a local dashboard) has the DB open: a passive checkpoint still copies
        # every committed page into the main file, which is what matters before a git commit
        logging.getLogger("atlas").warning("full checkpoint not possible (%s); doing a passive checkpoint", e.orig)
        with engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
    engine.dispose()


def rebuild_fts() -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO scholarships_fts(scholarships_fts) VALUES('rebuild')"))


def sync_sources() -> int:
    """Upsert the seed registry (config/sources.yaml) into the sources table."""
    from atlas.classify.source_classifier import is_official_type
    from atlas.urls import registered_domain

    n = 0
    with get_session() as s:
        for cfg in config.sources():
            reg = registered_domain(cfg["seeds"][0])
            row = s.query(Source).filter_by(registry_key=cfg["key"]).one_or_none()
            if row is None:
                row = Source(registry_key=cfg["key"], origin="SEED")
                s.add(row)
            row.name = cfg["name"]
            row.provider_name = cfg.get("provider")
            row.registered_domain = reg
            row.domain_aliases = json.dumps(cfg.get("domain_aliases", []))
            row.seeds = json.dumps(cfg["seeds"])
            row.sitemaps = json.dumps(cfg.get("sitemaps", []))
            row.source_type = cfg["source_type"]
            row.trust_tier = cfg.get("trust_tier", 3)
            row.role = cfg.get("role", "CRAWL")
            row.is_official = is_official_type(cfg["source_type"]) and row.role != "DISCOVERY_ONLY"
            if row.approval_status not in ("BROKEN", "BLOCKED"):
                row.approval_status = cfg.get("approval_status", "APPROVED")
            row.render_js = bool(cfg.get("render_js", False))
            row.max_depth = cfg.get("max_depth", 3)
            row.page_budget = cfg.get("page_budget", 120)
            row.base_frequency_hours = cfg.get("base_frequency_hours", 72)
            n += 1
        s.commit()
    return n
