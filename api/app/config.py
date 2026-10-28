import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    voices_dir: Path
    generations_dir: Path
    static_dir: Path
    default_variant: str
    max_upload_bytes: int
    preload_variants: tuple


def get_settings() -> Settings:
    return Settings(
        voices_dir=Path(os.environ.get("VOICES_DIR", "/data/voices")),
        generations_dir=Path(os.environ.get("GENERATIONS_DIR", "/data/generations")),
        static_dir=Path(os.environ.get("STATIC_DIR", "/app/static")),
        default_variant=os.environ.get("DEFAULT_VARIANT", "turbo"),
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))),
        preload_variants=tuple(
            v.strip() for v in os.environ.get("PRELOAD_VARIANTS", "").split(",") if v.strip()
        ),
    )
