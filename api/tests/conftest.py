import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app  # noqa: E402
from app.generations import GenerationStore  # noqa: E402
from app.voices import VoiceStore  # noqa: E402


class StubEngine:
    def __init__(self):
        self.calls = []
        self._loaded = set()

    def loaded(self):
        return sorted(self._loaded)

    def status(self):
        from app.models import VARIANTS

        return {
            v: {"state": "ready" if v in self._loaded else "idle", "error": None}
            for v in VARIANTS
        }

    def start_load(self, variant):
        if variant not in ("turbo", "standard", "multilingual"):
            raise ValueError(f"unknown variant '{variant}'")
        self._loaded.add(variant)
        return "loading"

    def synthesize(self, variant, text, ref_path=None, language="en", params=None):
        self._loaded.add(variant)
        self.calls.append(
            {"variant": variant, "text": text, "ref": ref_path, "language": language, "params": params or {}}
        )
        return np.zeros(2400, dtype=np.float32), 24000


@pytest.fixture
def engine():
    return StubEngine()


@pytest.fixture
def store(tmp_path):
    return VoiceStore(tmp_path / "voices")


@pytest.fixture
def gen_store(tmp_path):
    return GenerationStore(tmp_path / "generations")


@pytest.fixture
def client(engine, store, gen_store, tmp_path):
    return TestClient(
        create_app(engine=engine, store=store, gen_store=gen_store, static_dir=tmp_path / "no-ui")
    )
