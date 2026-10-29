import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.models import PARAM_SPECS, ModelManager, _load_variant, clean_params, resolve_variant


def test_nano_names_resolve():
    assert resolve_variant("nano") == "nano"
    assert resolve_variant("Chatterbox-Nano") == "nano"
    assert resolve_variant("chatterbox-turbo") == "turbo"


def test_nano_takes_turbo_params_and_rejects_classic_ones():
    assert PARAM_SPECS["nano"].keys() == PARAM_SPECS["turbo"].keys()
    assert clean_params("nano", {"top_k": 50, "norm_loudness": False}) == {"top_k": 50, "norm_loudness": False}
    with pytest.raises(ValueError):
        clean_params("nano", {"exaggeration": 0.5})


def test_loader_passes_nano_flag_to_the_stock_class(monkeypatch):
    calls = []

    class FakeTurbo:
        @staticmethod
        def from_pretrained(device, nano=False):
            calls.append((device, nano))
            return SimpleNamespace(sr=24000)

    fake = ModuleType("chatterbox.tts_turbo")
    fake.ChatterboxTurboTTS = FakeTurbo
    monkeypatch.setitem(sys.modules, "chatterbox", ModuleType("chatterbox"))
    monkeypatch.setitem(sys.modules, "chatterbox.tts_turbo", fake)
    _load_variant("turbo", "cpu")
    _load_variant("nano", "cpu")
    assert calls == [("cpu", False), ("cpu", True)]


def test_nano_conditionals_use_the_turbo_style_prepare(tmp_path):
    prepared = []

    class Model:
        sr = 24000
        conds = SimpleNamespace()

        def prepare_conditionals(self, path, **kwargs):
            prepared.append(kwargs)
            self.conds = SimpleNamespace()

        def generate(self, text, **kwargs):
            return [0.0] * 10

    ref = tmp_path / "a.wav"
    ref.write_bytes(b"x")
    ModelManager(loader=lambda v, d: Model()).synthesize("nano", "hi", ref_path=ref)
    assert prepared == [{"exaggeration": 0.0, "norm_loudness": True}]


def test_models_endpoint_lists_nano(client):
    models = {m["id"]: m for m in client.get("/api/models").json()["models"]}
    assert models["nano"]["label"] == "Nano" and models["nano"]["multilingual"] is False
    assert client.post("/api/models/nano/load").status_code == 202
    r = client.post("/api/tts", json={"model": "nano", "text": "hello"})
    assert r.status_code == 200
