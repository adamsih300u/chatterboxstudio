"""Voice store: reference clips on disk (one folder per voice), metadata in the database."""
import hashlib
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from .convert import file_duration
from .db import Database, VoiceRow

ALLOWED_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".m4b", ".aac", ".opus"}
DEFAULT_KEY = "default_voice"
HIDDEN_KEY = "builtin_hidden"
PREVIEW_PREFIX = "preview-"
PREVIEWS_PER_VOICE = 6


class VoiceError(ValueError):
    pass


class VoiceNotFound(VoiceError):
    pass


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    created: float
    filename: str
    path: Path
    updated: float = 0.0
    original: str = ""
    original_path: Optional[Path] = None
    regions: list = field(default_factory=list)

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "created": self.created,
            "updated": self.updated or self.created,
            "regions": self.regions,
            "has_original": bool(self.original),
            "duration": file_duration(self.path),
        }

    @property
    def source_path(self) -> Path:
        """What the clip editor re-trims: the untouched upload, or the clip for older voices."""
        return self.original_path or self.path


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise VoiceError("voice name must contain letters or digits")
    return slug[:64]


def _ext(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise VoiceError(f"unsupported file type '{ext}'")
    return ext


class VoiceStore:
    def __init__(self, root: Path, db: Optional[Database] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Standalone use (tests, scripts) keeps the database next to the clips.
        self.db = db or Database(self.root / "studio.db")

    def _voice(self, row: VoiceRow) -> Voice:
        folder = self.root / row.id
        return Voice(
            id=row.id,
            name=row.name,
            created=row.created,
            filename=row.filename,
            path=folder / row.filename,
            updated=row.updated,
            original=row.original,
            original_path=folder / row.original if row.original else None,
            regions=list(row.regions or []),
        )

    def add(
        self,
        name: str,
        data: bytes,
        filename: str,
        original: Optional[bytes] = None,
        original_filename: str = "",
        regions: Optional[list] = None,
    ) -> Voice:
        ext = _ext(filename)
        if not data:
            raise VoiceError("empty upload")
        orig_name = ""
        if original:
            orig_name = f"orig{_ext(original_filename)}"
        voice_id = slugify(name)
        folder = self.root / voice_id
        with self.db.session() as s:
            if s.get(VoiceRow, voice_id) is not None or folder.exists():
                raise VoiceError(f"voice '{voice_id}' already exists")
            clip = f"ref{ext}"
            folder.mkdir()
            try:
                (folder / clip).write_bytes(data)
                if orig_name:
                    (folder / orig_name).write_bytes(original)
                now = time.time()
                row = VoiceRow(
                    id=voice_id,
                    name=name.strip(),
                    created=now,
                    filename=clip,
                    original=orig_name,
                    regions=regions or [],
                    updated=now,
                )
                s.add(row)
                s.commit()
            except Exception:
                s.rollback()
                shutil.rmtree(folder, ignore_errors=True)
                raise
            return self._voice(row)

    def update(
        self,
        name: str,
        data: bytes,
        filename: str,
        regions: Optional[list] = None,
        original: Optional[bytes] = None,
        original_filename: str = "",
    ) -> Voice:
        """Replace a voice's clip (and optionally its original). The id and name stay the same.

        A voice saved before originals were kept has none: its current clip becomes the
        original, so later edits can start from it.
        """
        voice = self.get(name)
        if voice is None:
            raise VoiceNotFound(f"voice '{name}' not found")
        ext = _ext(filename)
        if not data:
            raise VoiceError("empty upload")
        orig_ext = _ext(original_filename) if original else ""
        folder = self.root / voice.id
        with self.db.session() as s:
            row = s.get(VoiceRow, voice.id)
            clip = f"ref{ext}"
            orig_name = row.original
            if original:
                orig_name = f"orig{orig_ext}"
            elif not orig_name:
                orig_name = f"orig{Path(row.filename).suffix}"
                shutil.copyfile(folder / row.filename, folder / orig_name)
            tmp = folder / ".ref.tmp"
            tmp.write_bytes(data)
            if original:
                (folder / ".orig.tmp").write_bytes(original)
            try:
                if original:
                    if row.original and row.original != orig_name:
                        (folder / row.original).unlink(missing_ok=True)
                    os.replace(folder / ".orig.tmp", folder / orig_name)
                if row.filename != clip:
                    (folder / row.filename).unlink(missing_ok=True)
                os.replace(tmp, folder / clip)
                row.filename = clip
                row.original = orig_name
                row.regions = regions or []
                row.updated = time.time()
                s.commit()
            except Exception:
                s.rollback()
                raise
            self.clear_previews(voice.id)
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

    # Preview cache: one wav per (clip, model, language, params, sentence), kept in the voice folder.

    def clip_digest(self, voice: Voice) -> str:
        return hashlib.sha256(voice.path.read_bytes()).hexdigest()

    def preview_path(self, voice: Voice, key: str) -> Path:
        return self.root / voice.id / f"{PREVIEW_PREFIX}{key}.wav"

    def save_preview(self, voice: Voice, key: str, wav: bytes) -> None:
        path = self.preview_path(voice, key)
        path.write_bytes(wav)
        old = sorted(path.parent.glob(f"{PREVIEW_PREFIX}*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in old[PREVIEWS_PER_VOICE:]:
            stale.unlink(missing_ok=True)

    def clear_previews(self, voice_id: str) -> None:
        for p in (self.root / voice_id).glob(f"{PREVIEW_PREFIX}*.wav"):
            p.unlink(missing_ok=True)

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
