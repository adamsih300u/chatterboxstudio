"""One-time import of the old on-disk layout (meta.json per folder, .default, .builtin_hidden).

Imported files are renamed with a ".migrated" suffix, never deleted, so the import is
idempotent and a deleted voice cannot be resurrected by a later start.
"""
import json
import logging
from pathlib import Path

from .db import Database, GenerationRow, VoiceRow
from .generations import ID_RE
from .voices import DEFAULT_KEY, HIDDEN_KEY

log = logging.getLogger(__name__)


def _mark_done(path: Path) -> None:
    path.rename(path.with_name(path.name + ".migrated"))


def import_legacy(db: Database, voices_root: Path, gens_root: Path) -> dict:
    counts = {"voices": 0, "generations": 0}
    voices_root, gens_root = Path(voices_root), Path(gens_root)

    if voices_root.is_dir():
        with db.session() as s:
            for folder in sorted(voices_root.iterdir()):
                meta_file = folder / "meta.json"
                if not meta_file.is_file():
                    continue
                try:
                    meta = json.loads(meta_file.read_text())
                    if s.get(VoiceRow, folder.name) is None:
                        s.add(
                            VoiceRow(
                                id=folder.name,
                                name=meta["name"],
                                created=meta["created"],
                                filename=meta["filename"],
                            )
                        )
                        counts["voices"] += 1
                    s.commit()
                except (OSError, ValueError, KeyError) as e:
                    s.rollback()
                    log.warning("skipping unreadable voice %s: %s", folder.name, e)
                    continue
                _mark_done(meta_file)

        default_file = voices_root / ".default"
        if default_file.is_file():
            db.set_setting(DEFAULT_KEY, default_file.read_text().strip() or None)
            _mark_done(default_file)
        hidden_file = voices_root / ".builtin_hidden"
        if hidden_file.is_file():
            db.set_setting(HIDDEN_KEY, "1")
            _mark_done(hidden_file)

    if gens_root.is_dir():
        with db.session() as s:
            for folder in sorted(gens_root.iterdir()):
                meta_file = folder / "meta.json"
                if not ID_RE.match(folder.name) or not meta_file.is_file():
                    continue
                try:
                    meta = json.loads(meta_file.read_text())
                    if s.get(GenerationRow, folder.name) is None:
                        s.add(
                            GenerationRow(
                                id=folder.name,
                                text=meta["text"],
                                model=meta.get("model", ""),
                                voice=meta.get("voice", ""),
                                language=meta.get("language", "en"),
                                created=meta["created"],
                                segments=meta.get("segments", []),
                                params=meta.get("params", {}),
                            )
                        )
                        counts["generations"] += 1
                    s.commit()
                except (OSError, ValueError, KeyError) as e:
                    s.rollback()
                    log.warning("skipping unreadable generation %s: %s", folder.name, e)
                    continue
                _mark_done(meta_file)

    if counts["voices"] or counts["generations"]:
        log.info("imported legacy data: %s", counts)
    return counts
