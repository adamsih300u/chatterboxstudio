"""Generation store: saved audio on disk, text and settings in the database."""
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from .convert import file_duration
from .db import Database, GenerationRow

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
            "duration": file_duration(self.path),
        }


class GenerationStore:
    def __init__(self, root: Path, db: Optional[Database] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Standalone use (tests, scripts) keeps the database next to the audio.
        self.db = db or Database(self.root / "studio.db")

    def _gen(self, row: GenerationRow) -> Generation:
        return Generation(
            id=row.id,
            text=row.text,
            model=row.model,
            voice=row.voice,
            language=row.language,
            created=row.created,
            path=self.root / row.id / "audio.wav",
            segments=row.segments or [],
            params=row.params or {},
        )

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
        try:
            (folder / "audio.wav").write_bytes(data)
            row = GenerationRow(
                id=gen_id,
                text=text,
                model=model,
                voice=voice,
                language=language,
                created=now,
                segments=segments or [],
                params=params or {},
            )
            with self.db.session() as s:
                s.add(row)
                s.commit()
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return self._gen(row)

    def list(self) -> list[Generation]:
        with self.db.session() as s:
            rows = s.scalars(select(GenerationRow).order_by(GenerationRow.created.desc()))
            return [self._gen(r) for r in rows]

    def get(self, gen_id: str) -> Optional[Generation]:
        if not ID_RE.match(gen_id):
            return None
        with self.db.session() as s:
            row = s.get(GenerationRow, gen_id)
            return self._gen(row) if row else None

    def delete(self, gen_id: str) -> bool:
        gen = self.get(gen_id)
        if gen is None:
            return False
        with self.db.session() as s:
            s.delete(s.get(GenerationRow, gen.id))
            s.commit()
        shutil.rmtree(self.root / gen.id, ignore_errors=True)
        return True
