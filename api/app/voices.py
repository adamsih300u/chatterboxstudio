"""Voice store: reference clips plus metadata on disk (one folder per voice)."""
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ALLOWED_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


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
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def add(self, name: str, data: bytes, filename: str) -> Voice:
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTS:
            raise VoiceError(f"unsupported file type '{ext}'")
        if not data:
            raise VoiceError("empty upload")
        voice_id = slugify(name)
        folder = self.root / voice_id
        if folder.exists():
            raise VoiceError(f"voice '{voice_id}' already exists")
        folder.mkdir()
        clip = f"ref{ext}"
        (folder / clip).write_bytes(data)
        meta = {"name": name.strip(), "created": time.time(), "filename": clip}
        (folder / "meta.json").write_text(json.dumps(meta))
        return self._load(folder)

    def _load(self, folder: Path) -> Voice:
        meta = json.loads((folder / "meta.json").read_text())
        return Voice(
            id=folder.name,
            name=meta["name"],
            created=meta["created"],
            filename=meta["filename"],
            path=folder / meta["filename"],
        )

    def list(self) -> list[Voice]:
        voices = []
        for folder in sorted(self.root.iterdir()):
            if (folder / "meta.json").is_file():
                voices.append(self._load(folder))
        return voices

    def get(self, name: str) -> Optional[Voice]:
        try:
            folder = self.root / slugify(name)
        except VoiceError:
            return None
        return self._load(folder) if (folder / "meta.json").is_file() else None

    def delete(self, name: str) -> bool:
        voice = self.get(name)
        if voice is None:
            return False
        shutil.rmtree(self.root / voice.id)
        if self.default_id() is None:
            self._default_file.unlink(missing_ok=True)
        return True

    @property
    def _default_file(self) -> Path:
        # A file (not a folder), so list() never mistakes it for a voice.
        return self.root / ".default"

    def default_id(self) -> Optional[str]:
        """Id of the voice preselected in the UI, or None if unset or the voice is gone."""
        try:
            voice = self.get(self._default_file.read_text().strip())
        except OSError:
            return None
        return voice.id if voice else None

    @property
    def _hidden_file(self) -> Path:
        return self.root / ".builtin_hidden"

    def builtin_hidden(self) -> bool:
        """True when the built-in voice is hidden from the UI dropdown and {tag} menus."""
        return self._hidden_file.is_file()

    def set_builtin_hidden(self, hidden: bool) -> bool:
        if hidden:
            self._hidden_file.write_text("1")
        else:
            self._hidden_file.unlink(missing_ok=True)
        return hidden

    def set_default(self, name: Optional[str]) -> Optional[str]:
        if not name:
            self._default_file.unlink(missing_ok=True)
            return None
        voice = self.get(name)
        if voice is None:
            raise VoiceError(f"voice '{name}' not found")
        self._default_file.write_text(voice.id)
        return voice.id
