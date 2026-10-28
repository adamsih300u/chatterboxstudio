"""Generation store: saved audio plus the text and settings that produced it."""
import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ID_RE = re.compile(r"^[0-9]{14}-[0-9a-f]{8}$")


@dataclass(frozen=True)
class Generation:
    id: str
    text: str
    model: str
    voice: str
    language: str
    created: float
    path: Path
    segments: list = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "model": self.model,
            "voice": self.voice,
            "language": self.language,
            "created": self.created,
            "segments": self.segments,
            "params": self.params,
        }


class GenerationStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def add(
        self,
        text: str,
        model: str,
        voice: str,
        language: str,
        data: bytes,
        segments: Optional[list] = None,
        params: Optional[dict] = None,
    ) -> Generation:
        if not data:
            raise ValueError("empty audio")
        if not text.strip():
            raise ValueError("text is empty")
        now = time.time()
        gen_id = f"{time.strftime('%Y%m%d%H%M%S', time.gmtime(now))}-{uuid.uuid4().hex[:8]}"
        folder = self.root / gen_id
        folder.mkdir()
        (folder / "audio.wav").write_bytes(data)
        meta = {
            "text": text,
            "model": model,
            "voice": voice,
            "language": language,
            "created": now,
            "segments": segments or [],
            "params": params or {},
        }
        (folder / "meta.json").write_text(json.dumps(meta))
        return self._load(folder)

    def _load(self, folder: Path) -> Generation:
        meta = json.loads((folder / "meta.json").read_text())
        return Generation(
            id=folder.name,
            text=meta["text"],
            model=meta.get("model", ""),
            voice=meta.get("voice", ""),
            language=meta.get("language", "en"),
            created=meta["created"],
            path=folder / "audio.wav",
            segments=meta.get("segments", []),
            params=meta.get("params", {}),
        )

    def list(self) -> list[Generation]:
        items = [
            self._load(f)
            for f in self.root.iterdir()
            if ID_RE.match(f.name) and (f / "meta.json").is_file()
        ]
        return sorted(items, key=lambda g: g.created, reverse=True)

    def get(self, gen_id: str) -> Optional[Generation]:
        if not ID_RE.match(gen_id):
            return None
        folder = self.root / gen_id
        return self._load(folder) if (folder / "meta.json").is_file() else None

    def delete(self, gen_id: str) -> bool:
        gen = self.get(gen_id)
        if gen is None:
            return False
        shutil.rmtree(self.root / gen.id)
        return True
