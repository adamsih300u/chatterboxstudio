from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

VariantName = Literal["turbo", "standard", "multilingual"]


class Settings(BaseSettings):
    """Typed environment config. Field names map to upper-case env vars (VOICES_DIR, ...).

    A bad value (PRELOAD_VARIANTS=turbo,bogus, MAX_UPLOAD_BYTES=lots) stops startup
    with a message naming the variable.
    """

    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    voices_dir: Path = Path("/data/voices")
    generations_dir: Path = Path("/data/generations")
    database_path: Path = Path("/data/studio.db")
    static_dir: Path = Path("/app/static")
    default_variant: VariantName = "turbo"
    max_upload_bytes: int = Field(20 * 1024 * 1024, gt=0)
    # Uploads sent to /api/audio/decode, which may be whole audiobooks.
    max_decode_bytes: int = Field(300 * 1024 * 1024, gt=0)
    max_text_chars: int = Field(10_000, gt=0)
    # Sentence spoken by the voice preview. Changing it re-renders every cached preview.
    preview_text: str = Field(
        "Hello, this is a quick sample of my voice. How does it sound to you?", min_length=1, max_length=500
    )
    # Comma-separated in the environment, e.g. PRELOAD_VARIANTS=turbo,standard
    preload_variants: Annotated[tuple[VariantName, ...], NoDecode] = ()

    @field_validator("preload_variants", mode="before")
    @classmethod
    def _split_variants(cls, v):
        if isinstance(v, str):
            return tuple(p.strip() for p in v.split(",") if p.strip())
        return v


def get_settings() -> Settings:
    # Read on every call so tests can monkeypatch the environment between apps.
    return Settings()
