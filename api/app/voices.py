"""Voice store: reference clips on disk (one folder per voice), metadata in the database."""
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from .db import Database, VoiceRow

ALLOWED_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
DEFAULT_KEY = "default_voice"
HIDDEN_KEY = "builtin_hidden"


class VoiceError(ValueError):
    pass


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    created: float
    filename: str
    path: Path

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "created": self.created}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise VoiceError("voice name must contain letters or digits")
    return slug[:64]


class VoiceStore:
    def __init__(self, root: Path, db: Optional[Database] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Standalone use (tests, scripts) keeps the database next to the clips.
        self.db = db or Database(self.root / "studio.db")

    def _voice(self, row: VoiceRow) -> Voice:
        return Voice(
            id=row.id,
            name=row.name,
            created=row.created,
            filename=row.filename,
            path=self.root / row.id / row.filename,
        )

    def add(self, name: str, data: bytes, filename: str) -> Voice:
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTS:
            raise VoiceError(f"unsupported file type '{ext}'")
        if not data:
            raise VoiceError("empty upload")
        voice_id = slugify(name)
        folder = self.root / voice_id
        with self.db.session() as s:
            if s.get(VoiceRow, voice_id) is not None or folder.exists():
                raise VoiceError(f"voice '{voice_id}' already exists")
            clip = f"ref{ext}"
            folder.mkdir()
            try:
                (folder / clip).write_bytes(data)
                row = VoiceRow(id=voice_id, name=name.strip(), created=time.time(), filename=clip)
                s.add(row)
                s.commit()
            except Exception:
                s.rollback()
                shutil.rmtree(folder, ignore_errors=True)
                raise
            return self._voice(row)

    def list(self) -> list[Voice]:
        with self.db.session() as s:
            return [self._voice(r) for r in s.scalars(select(VoiceRow).order_by(VoiceRow.id))]

    def get(self, name: str) -> Optional[Voice]:
        try:
            voice_id = slugify(name)
        except VoiceError:
            return None
        with self.db.session() as s:
            row = s.get(VoiceRow, voice_id)
            return self._voice(row) if row else None

    def delete(self, name: str) -> bool:
        voice = self.get(name)
        if voice is None:
            return False
        with self.db.session() as s:
            s.delete(s.get(VoiceRow, voice.id))
            s.commit()
        shutil.rmtree(self.root / voice.id, ignore_errors=True)
        if self.default_id() is None:
            self.db.set_setting(DEFAULT_KEY, None)
        return True

    def default_id(self) -> Optional[str]:
        """Id of the voice preselected in the UI, or None if unset or the voice is gone."""
        stored = self.db.get_setting(DEFAULT_KEY)
        voice = self.get(stored) if stored else None
        return voice.id if voice else None

    def builtin_hidden(self) -> bool:
        """True when the built-in voice is hidden from the UI dropdown and {tag} menus."""
        return self.db.get_setting(HIDDEN_KEY) == "1"

    def set_builtin_hidden(self, hidden: bool) -> bool:
        self.db.set_setting(HIDDEN_KEY, "1" if hidden else None)
        return hidden

    def set_default(self, name: Optional[str]) -> Optional[str]:
        if not name:
            self.db.set_setting(DEFAULT_KEY, None)
            return None
        voice = self.get(name)
        if voice is None:
            raise VoiceError(f"voice '{name}' not found")
        self.db.set_setting(DEFAULT_KEY, voice.id)
        return voice.id
