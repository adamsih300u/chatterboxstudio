import pytest

from app.models import ModelManager, resolve_variant


class FakeModel:
    sr = 24000

    def generate(self, text, **kwargs):
        self.kwargs = kwargs
        return [0.0] * 10


def test_lazy_load_once_per_variant():
    loads = []

    def loader(variant, device):
        loads.append(variant)
        return FakeModel()

    mm = ModelManager(loader=loader)
    assert mm.loaded() == []
    mm.synthesize("turbo", "a")
    mm.synthesize("turbo", "b")
    mm.synthesize("standard", "c")
    assert loads == ["turbo", "standard"]


def test_multilingual_passes_language_id():
    model = FakeModel()
    mm = ModelManager(loader=lambda v, d: model)
    mm.synthesize("multilingual", "hola", language="es")
    assert model.kwargs["language_id"] == "es"


def test_resolve_variant():
    assert resolve_variant("tts-1") == "turbo"
    assert resolve_variant("chatterbox") == "standard"
    with pytest.raises(ValueError):
        resolve_variant("gpt-4")
