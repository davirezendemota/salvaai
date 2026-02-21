"""Engine e sessão SQLite para uso síncrono (bot chama em to_thread)."""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine, select

from src.db.models import Plan

_engine = None


def get_database_url() -> str:
    url = (os.getenv("DATABASE_URL") or os.getenv("SQLITE_PATH") or "").strip()
    if not url:
        url = "sqlite:///./data/salvaai.db"
    if url.startswith("sqlite") and ":///" not in url.replace("sqlite:///", ""):
        Path(url.replace("sqlite:///", "").split("?")[0]).parent.mkdir(parents=True, exist_ok=True)
    return url


def get_engine():
    global _engine
    if _engine is None:
        url = get_database_url()
        _engine = create_engine(url, connect_args={"check_same_thread": False})
    return _engine


@contextmanager
def get_session() -> Generator[Session, None, None]:
    engine = get_engine()
    with Session(engine) as session:
        yield session


def create_all_tables() -> None:
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _migrate_usage_video_link(engine)


def _migrate_usage_video_link(engine) -> None:
    """Adiciona coluna video_link na tabela usage se não existir (SQLite)."""
    from sqlalchemy import text
    url = str(engine.url)
    if not url.startswith("sqlite"):
        return
    with engine.connect() as conn:
        try:
            r = conn.execute(text("PRAGMA table_info(usage)"))
            columns = [row[1] for row in r.fetchall()]
        except Exception:
            return
        if "video_link" in columns:
            return
        try:
            conn.execute(text("ALTER TABLE usage ADD COLUMN video_link VARCHAR(2048)"))
            conn.commit()
        except Exception:
            conn.rollback()


def seed_plans_if_empty() -> None:
    """Insere planos Basic, Pro, Creator se a tabela plan estiver vazia."""
    with get_session() as session:
        if session.exec(select(Plan)).first() is not None:
            return
        plans = [
            Plan(slug="basic", name="Básico", price_cents=1000, posts_included=20),
            Plan(slug="pro", name="Pro", price_cents=2500, posts_included=60),
            Plan(slug="creator", name="Creator", price_cents=4500, posts_included=120),
        ]
        for p in plans:
            session.add(p)
        session.commit()


def seed_test_balance_if_set() -> None:
    """Em ambiente de teste (TEST_BALANCE_POSTS definido), define saldo de todos os usuários."""
    import os
    raw = (os.getenv("TEST_BALANCE_POSTS") or "").strip()
    if not raw:
        return
    try:
        balance = max(0, int(raw))
    except ValueError:
        return
    from src.db.models import User
    with get_session() as session:
        users = list(session.exec(select(User)))
        for user in users:
            user.balance_posts = balance
            session.add(user)
        if users:
            session.commit()
