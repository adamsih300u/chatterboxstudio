"""Voice store: clips on disk (one folder per voice), metadata in the database.

A voice has one or more moods. A mood is a reference clip (neutral, angry, whisper, ...) that was
cut from the voice's sources: the untrimmed uploads kept alongside it. Scripts pick a mood with
{angry} or {Alice:angry}; a voice's own name, {Alice}, plays its neutral mood.
"""
import hashlib
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, select

from .convert import file_duration
from .db import Database, VoiceMoodRow, VoiceRow, VoiceSourceRow

ALLOWED_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".m4b", ".aac", ".opus"}
DEFAULT_KEY = "default_voice"
HIDDEN_KEY = "builtin_hidden"
PREVIEW_PREFIX = "preview-"
PREVIEWS_PER_VOICE = 6
NEUTRAL = "neutral"
# Words with a meaning of their own in a {tag}.
RESERVED_MOODS = {"default", "none", "cut"}
MAX_MOODS = 24
MAX_SOURCES = 20


class VoiceError(ValueError):
    pass


class VoiceNotFound(VoiceError):
    pass


@dataclass(frozen=True)
class Mood:
    id: str
    name: str
    filename: str
    path: Path
    regions: list = field(default_factory=list)
    updated: float = 0.0

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "regions": self.regions,
            "updated": self.updated,
            "duration": file_duration(self.path),
        }


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    filename: str
    path: Path
    created: float = 0.0

    def public(self) -> dict:
        return {"id": self.id, "name": self.label or self.filename, "duration": file_duration(self.path)}


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    created: float
    updated: float = 0.0
    moods: tuple = ()
    sources: tuple = ()

    @property
    def neutral(self) -> Mood:
        return self.mood(NEUTRAL) or self.moods[0]

    def mood(self, name: str) -> Optional[Mood]:
        try:
            wanted = slugify(name)
        except VoiceError:
            return None
        return next((m for m in self.moods if m.id == wanted), None)

    def source(self, source_id: str) -> Optional[Source]:
        return next((s for s in self.sources if s.id == source_id), None)

    # The single-clip view older code and clients use: the neutral mood and the first source.
    @property
    def path(self) -> Path:
        return self.neutral.path

    @property
    def filename(self) -> str:
        return self.neutral.filename

    @property
    def regions(self) -> list:
        return self.neutral.regions

    @property
    def original(self) -> str:
        return self.sources[0].filename if self.sources else ""

    @property
    def original_path(self) -> Optional[Path]:
        return self.sources[0].path if self.sources else None

    @property
    def source_path(self) -> Path:
        """What the clip editor re-trims: the first upload, or the neutral clip for older voices."""
        return self.original_path or self.path

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "created": self.created,
            "updated": self.updated or self.created,
            "regions": self.regions,
            "has_original": bool(self.sources),
            "duration": file_duration(self.path),
            "moods": [m.public() for m in self.moods],
            "sources": [s.public() for s in self.sources],
        }


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise VoiceError("name must contain letters or digits")
    return slug[:64]


def mood_id(name: str) -> str:
    slug = slugify(name)
    if slug in RESERVED_MOODS:
        raise VoiceError(f"'{name.strip()}' is reserved; pick another mood name")
    return slug


def _ext(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise VoiceError(f"unsupported file type '{ext}'")
    return ext


def _write(path: Path, data: bytes) -> None:
    """Write through a temp file so a crash never leaves half a clip behind."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class VoiceStore:
    def __init__(self, root: Path, db: Optional[Database] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Standalone use (tests, scripts) keeps the database next to the clips.
        self.db = db or Database(self.root / "studio.db")

    # -- reading ------------------------------------------------------------------------------

    def _voice(self, s, row: VoiceRow) -> Voice:
        folder = self.root / row.id
        moods = s.scalars(select(VoiceMoodRow).where(VoiceMoodRow.voice_id == row.id).order_by(VoiceMoodRow.created, VoiceMoodRow.id))
        sources = s.scalars(select(VoiceSourceRow).where(VoiceSourceRow.voice_id == row.id).order_by(VoiceSourceRow.created, VoiceSourceRow.id))
        mood_list = [
            Mood(m.id, m.name, m.filename, folder / m.filename, list(m.regions or []), m.updated)
            for m in moods
        ]
        # Neutral first, so it is what a bare voice name plays and the first thing listed.
        mood_list.sort(key=lambda m: m.id != NEUTRAL)
        return Voice(
            id=row.id,
            name=row.name,
            created=row.created,
            updated=row.updated,
            moods=tuple(mood_list),
            sources=tuple(Source(x.id, x.label, x.filename, folder / x.filename, x.created) for x in sources),
        )

    def list(self) -> list[Voice]:
        with self.db.session() as s:
            return [self._voice(s, r) for r in s.scalars(select(VoiceRow).order_by(VoiceRow.id))]

    def get(self, name: str) -> Optional[Voice]:
        try:
            voice_id = slugify(name)
        except VoiceError:
            return None
        with self.db.session() as s:
            row = s.get(VoiceRow, voice_id)
            return self._voice(s, row) if row else None

    def _require(self, name: str) -> Voice:
        voice = self.get(name)
        if voice is None:
            raise VoiceNotFound(f"voice '{name}' not found")
        return voice

    # -- voices -------------------------------------------------------------------------------

    def add(
        self,
        name: str,
        data: bytes,
        filename: str,
        original: Optional[bytes] = None,
        original_filename: str = "",
        regions: Optional[list] = None,
    ) -> Voice:
        """Create a voice from a clip (its neutral mood), optionally with the untrimmed upload it came from."""
        ext = _ext(filename)
        if not data:
            raise VoiceError("empty upload")
        orig_ext = _ext(original_filename) if original else ""
        voice_id = slugify(name)
        folder = self.root / voice_id
        with self.db.session() as s:
            if s.get(VoiceRow, voice_id) is not None or folder.exists():
                raise VoiceError(f"voice '{voice_id}' already exists")
            folder.mkdir()
            try:
                now = time.time()
                s.add(VoiceRow(id=voice_id, name=name.strip(), created=now, updated=now))
                s.flush()
                _write(folder / f"ref{ext}", data)
                keep = list(regions or [])
                if original:
                    _write(folder / f"orig{orig_ext}", original)
                    s.add(VoiceSourceRow(voice_id=voice_id, id="s1", filename=f"orig{orig_ext}", label=original_filename, created=now))
                    keep = [{"source": "s1", **r} if not r.get("source") else r for r in keep]
                s.add(VoiceMoodRow(voice_id=voice_id, id=NEUTRAL, name="Neutral", filename=f"ref{ext}", regions=keep, created=now, updated=now))
                s.commit()
            except Exception:
                s.rollback()
                shutil.rmtree(folder, ignore_errors=True)
                raise
        return self._require(voice_id)

    def update(
        self,
        name: str,
        data: bytes,
        filename: str,
        regions: Optional[list] = None,
        original: Optional[bytes] = None,
        original_filename: str = "",
    ) -> Voice:
        """Replace a voice's neutral clip (and optionally its first source). The id and name stay the same.

        A voice saved before originals were kept has none: its current clip becomes the
        original, so later edits can start from it.
        """
        voice = self._require(name)
        ext = _ext(filename)
        if not data:
            raise VoiceError("empty upload")
        orig_ext = _ext(original_filename) if original else ""
        folder = self.root / voice.id
        first = voice.sources[0] if voice.sources else None
        with self.db.session() as s:
            row = s.get(VoiceRow, voice.id)
            moodrow = s.get(VoiceMoodRow, (voice.id, voice.neutral.id))
            if original or first is None:
                # Replace the first source with the new upload, or keep the old clip as one.
                body = original if original else voice.neutral.path.read_bytes()
                src_ext = orig_ext if original else voice.neutral.path.suffix
                src = s.get(VoiceSourceRow, (voice.id, "s1")) if first else None
                if src is None:
                    src = VoiceSourceRow(voice_id=voice.id, id="s1", filename="", label="", created=time.time())
                    s.add(src)
                if src.filename and src.filename != f"orig{src_ext}":
                    (folder / src.filename).unlink(missing_ok=True)
                _write(folder / f"orig{src_ext}", body)
                src.filename = f"orig{src_ext}"
                if original:
                    src.label = original_filename
                first_id = src.id
            else:
                first_id = first.id
            keep = [r if r.get("source") else {"source": first_id, **r} for r in (regions or [])]
            clip = f"ref{ext}" if moodrow.id == NEUTRAL else f"mood-{moodrow.id}{ext}"
            if moodrow.filename != clip:
                (folder / moodrow.filename).unlink(missing_ok=True)
            _write(folder / clip, data)
            moodrow.filename, moodrow.regions, moodrow.updated = clip, keep, time.time()
            row.updated = moodrow.updated
            s.commit()
        self.clear_previews(voice.id)
        return self._require(voice.id)

    def delete(self, name: str) -> bool:
        voice = self.get(name)
        if voice is None:
            return False
        with self.db.session() as s:
            s.execute(delete(VoiceMoodRow).where(VoiceMoodRow.voice_id == voice.id))
            s.execute(delete(VoiceSourceRow).where(VoiceSourceRow.voice_id == voice.id))
            s.delete(s.get(VoiceRow, voice.id))
            s.commit()
        shutil.rmtree(self.root / voice.id, ignore_errors=True)
        if self.default_id() is None:
            self.db.set_setting(DEFAULT_KEY, None)
        return True

    # -- sources ------------------------------------------------------------------------------

    def add_source(self, name: str, data: bytes, filename: str) -> Source:
        """Keep another untrimmed upload with a voice, to cut clips from."""
        voice = self._require(name)
        ext = _ext(filename)
        if not data:
            raise VoiceError("empty upload")
        if len(voice.sources) >= MAX_SOURCES:
            raise VoiceError(f"a voice can keep at most {MAX_SOURCES} uploads")
        taken = {int(x.id[1:]) for x in voice.sources if x.id[1:].isdigit()}
        number = next(n for n in range(1, MAX_SOURCES + len(taken) + 2) if n not in taken)
        source_id = f"s{number}"
        stored = f"orig{ext}" if number == 1 else f"src-{source_id}{ext}"
        with self.db.session() as s:
            _write(self.root / voice.id / stored, data)
            s.add(VoiceSourceRow(voice_id=voice.id, id=source_id, filename=stored, label=Path(filename).name[:128], created=time.time()))
            s.commit()
        return self._require(voice.id).source(source_id)

    def remove_source(self, name: str, source_id: str) -> None:
        """Delete an upload. Refused while a mood's regions are cut from it."""
        voice = self._require(name)
        source = voice.source(source_id)
        if source is None:
            raise VoiceNotFound(f"upload '{source_id}' not found")
        users = [m.name for m in voice.moods if any(r.get("source") == source_id for r in m.regions)]
        if users:
            raise VoiceError(f"upload '{source_id}' is used by {', '.join(users)}; re-cut those moods first")
        with self.db.session() as s:
            s.delete(s.get(VoiceSourceRow, (voice.id, source_id)))
            s.commit()
        source.path.unlink(missing_ok=True)

    # -- moods --------------------------------------------------------------------------------

    def set_mood(self, name: str, mood_name: str, data: bytes, filename: str, regions: Optional[list] = None) -> Voice:
        """Create a mood or replace its clip. `regions` records which parts of which uploads it was cut from."""
        voice = self._require(name)
        mid = mood_id(mood_name)
        if mid == NEUTRAL:
            return self.update(voice.id, data, filename, regions=regions) if voice.sources else self._set_neutral(voice, data, filename, regions)
        ext = _ext(filename)
        if not data:
            raise VoiceError("empty upload")
        known = {x.id for x in voice.sources}
        for r in regions or []:
            if r.get("source") not in known:
                raise VoiceError(f"region refers to an unknown upload '{r.get('source')}'")
        existing = voice.mood(mid)
        if existing is None and len(voice.moods) >= MAX_MOODS:
            raise VoiceError(f"a voice can have at most {MAX_MOODS} moods")
        clip = f"mood-{mid}{ext}"
        folder = self.root / voice.id
        with self.db.session() as s:
            row = s.get(VoiceMoodRow, (voice.id, mid))
            now = time.time()
            if existing and existing.filename != clip:
                (folder / existing.filename).unlink(missing_ok=True)
            _write(folder / clip, data)
            if row is None:
                s.add(VoiceMoodRow(voice_id=voice.id, id=mid, name=mood_name.strip()[:64], filename=clip, regions=list(regions or []), created=now, updated=now))
            else:
                row.filename, row.regions, row.updated = clip, list(regions or []), now
            s.get(VoiceRow, voice.id).updated = now
            s.commit()
        self.clear_previews(voice.id)
        return self._require(voice.id)

    def _set_neutral(self, voice: Voice, data: bytes, filename: str, regions: Optional[list]) -> Voice:
        # No uploads kept yet: regions have nothing to point at, so only the clip is replaced.
        return self.update(voice.id, data, filename, regions=None) if not regions else self.update(voice.id, data, filename, regions=regions)

    def remove_mood(self, name: str, mood_name: str) -> Voice:
        voice = self._require(name)
        mood = voice.mood(mood_name)
        if mood is None:
            raise VoiceNotFound(f"mood '{mood_name}' not found")
        if mood.id == NEUTRAL:
            raise VoiceError("the neutral mood is what the voice's own name plays; replace it instead of deleting it")
        with self.db.session() as s:
            s.delete(s.get(VoiceMoodRow, (voice.id, mood.id)))
            s.commit()
        mood.path.unlink(missing_ok=True)
        self.clear_previews(voice.id)
        return self._require(voice.id)

    # -- previews -----------------------------------------------------------------------------
    # One wav per (clip, model, language, params, sentence), kept in the voice folder.

    def clip_digest(self, clip) -> str:
        """Fingerprint of a clip; `clip` is a Voice (its neutral clip) or a Mood."""
        return hashlib.sha256(clip.path.read_bytes()).hexdigest()

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

    # -- settings -----------------------------------------------------------------------------

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


def resolve_spec(store: VoiceStore, spec: str, base: Optional[Voice]) -> tuple[Voice, Mood]:
    """Turn what a {tag} or a request names into (voice, mood). Raises VoiceNotFound.

    "Alice:angry" is exact. A bare name is looked up as a mood of the voice already speaking
    first, so {angry} works in a one-voice script, then as a voice (its neutral mood).
    """
    spec = spec.strip()
    if ":" in spec:
        voice_name, _, mood_name = spec.partition(":")
        voice = store.get(voice_name)
        if voice is None:
            raise VoiceNotFound(f"voice '{voice_name.strip()}' not found")
        mood = voice.mood(mood_name)
        if mood is None:
            raise VoiceNotFound(f"voice '{voice.name}' has no mood '{mood_name.strip()}'")
        return voice, mood
    if base is not None:
        mood = base.mood(spec)
        if mood is not None:
            return base, mood
    voice = store.get(spec)
    if voice is None:
        hint = f" (or a mood of {base.name})" if base is not None else ""
        raise VoiceNotFound(f"no voice{hint} named '{spec}'")
    return voice, voice.neutral
