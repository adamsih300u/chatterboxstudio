"""SQLite metadata database (SQLAlchemy 2 + Alembic). Audio files stay on disk."""
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Float, String, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class VoiceRow(Base):
    __tablename__ = "voices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    created: Mapped[float] = mapped_column(Float)
    filename: Mapped[str] = mapped_column(String(32))
    # Untrimmed upload kept so the clip can be re-trimmed later; "" for voices saved before this existed.
    original: Mapped[str] = mapped_column(String(32), default="", server_default="")
    # Keep-regions [{start, end}] in seconds of the original, as last saved from the clip editor.
    regions: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    updated: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")


class GenerationRow(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(32), default="")
    voice: Mapped[str] = mapped_column(String(256), default="")
    language: Mapped[str] = mapped_column(String(16), default="en")
    created: Mapped[float] = mapped_column(Float, index=True)
    segments: Mapped[list] = mapped_column(JSON, default=list)
    params: Mapped[dict] = mapped_column(JSON, default=dict)


class SettingRow(Base):
    """Small key/value table: default voice, built-in voice visibility."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


MIGRATIONS = Path(__file__).parent / "migrations"


def _set_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


class Database:
    """Owns the engine, applies migrations on open, and hands out short-lived sessions."""

    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine: Engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False}
        )
        event.listen(self.engine, "connect", _set_pragmas)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.migrate()

    def migrate(self) -> None:
        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS))
        cfg.attributes["engine"] = self.engine
        command.upgrade(cfg, "head")

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as s:
            yield s

    def get_setting(self, key: str) -> Optional[str]:
        with self.session() as s:
            row = s.get(SettingRow, key)
            return row.value if row else None

    def set_setting(self, key: str, value: Optional[str]) -> None:
        """Store a value; None removes the key."""
        with self.session() as s:
            row = s.get(SettingRow, key)
            if value is None:
                if row:
                    s.delete(row)
            elif row:
                row.value = value
            else:
                s.add(SettingRow(key=key, value=value))
            s.commit()

    def dispose(self) -> None:
        self.engine.dispose()


__all__ = ["Base", "Database", "GenerationRow", "SettingRow", "VoiceRow"]
