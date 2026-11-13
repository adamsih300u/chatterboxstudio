"""SQLite metadata database (SQLAlchemy 2 + Alembic). Audio files stay on disk."""
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class VoiceRow(Base):
    __tablename__ = "voices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    created: Mapped[float] = mapped_column(Float)
    updated: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")


class VoiceSourceRow(Base):
    """An untrimmed upload kept with a voice, so clips can be cut from it (and from later uploads)."""

    __tablename__ = "voice_sources"

    voice_id: Mapped[str] = mapped_column(String(64), ForeignKey("voices.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # s1, s2, ...
    filename: Mapped[str] = mapped_column(String(48))
    label: Mapped[str] = mapped_column(String(128), default="")
    created: Mapped[float] = mapped_column(Float)


class VoiceMoodRow(Base):
    """One reference clip of a voice (neutral, angry, whisper, ...). The clip is cut from sources."""

    __tablename__ = "voice_moods"

    voice_id: Mapped[str] = mapped_column(String(64), ForeignKey("voices.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # the slug used in {tags}
    name: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(48))
    # Keep-regions [{source, start, end}] in seconds of the named source, as last saved from the editor.
    regions: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    created: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
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
    # Sampling seed the clip was generated with; None for clips saved before seeds existed.
    seed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


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


__all__ = ["Base", "Database", "GenerationRow", "SettingRow", "VoiceMoodRow", "VoiceRow", "VoiceSourceRow"]
