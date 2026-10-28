from types import SimpleNamespace

from app.models import ModelManager


class CondModel:
    """Mimics upstream: prepare_conditionals() sets .conds, generate() reads it."""

    sr = 24000

    def __init__(self):
        self.conds = SimpleNamespace(voice="builtin", t3="t3")
        self.prepared = []
        self.used = []

    def prepare_conditionals(self, path, **kwargs):
        self.prepared.append((path, kwargs))
        self.conds = SimpleNamespace(voice=open(path, "rb").read().decode(), t3="t3")

    def generate(self, text, **kwargs):
        assert "audio_prompt_path" not in kwargs
        self.used.append(self.conds.voice)
        return [0.0] * 10


def _ref(tmp_path, name):
    p = tmp_path / f"{name}.wav"
    p.write_bytes(name.encode())
    return p


def test_voice_prepared_once_and_reused(tmp_path):
    model = CondModel()
    mm = ModelManager(loader=lambda v, d: model)
    alice = _ref(tmp_path, "alice")
    for _ in range(3):
        mm.synthesize("standard", "hi", ref_path=alice)
    assert len(model.prepared) == 1
    assert model.used == ["alice"] * 3


def test_no_voice_request_gets_builtin_voice_back(tmp_path):
    model = CondModel()
    mm = ModelManager(loader=lambda v, d: model)
    alice, bob = _ref(tmp_path, "alice"), _ref(tmp_path, "bob")
    mm.synthesize("standard", "a", ref_path=alice)
    mm.synthesize("standard", "b")
    mm.synthesize("standard", "c", ref_path=bob)
    mm.synthesize("standard", "d", ref_path=alice)
    assert model.used == ["alice", "builtin", "bob", "alice"]
    assert len(model.prepared) == 2


def test_changed_file_invalidates_cache(tmp_path):
    model = CondModel()
    mm = ModelManager(loader=lambda v, d: model)
    ref = _ref(tmp_path, "alice")
    mm.synthesize("standard", "a", ref_path=ref)
    ref.write_bytes(b"alice, re-recorded")
    mm.synthesize("standard", "b", ref_path=ref)
    assert model.used == ["alice", "alice, re-recorded"]


def test_turbo_keys_on_norm_loudness_and_passes_it_to_prepare(tmp_path):
    model = CondModel()
    mm = ModelManager(loader=lambda v, d: model)
    ref = _ref(tmp_path, "alice")
    mm.synthesize("turbo", "a", ref_path=ref)
    mm.synthesize("turbo", "b", ref_path=ref, params={"norm_loudness": False})
    mm.synthesize("turbo", "c", ref_path=ref, params={"norm_loudness": False})
    assert [k["norm_loudness"] for _, k in model.prepared] == [True, False]
    assert all(k["exaggeration"] == 0.0 for _, k in model.prepared)


def test_exaggeration_change_does_not_corrupt_cached_entry(tmp_path):
    model = CondModel()
    mm = ModelManager(loader=lambda v, d: model)
    ref = _ref(tmp_path, "alice")
    mm.synthesize("standard", "a", ref_path=ref, params={"exaggeration": 0.9})
    model.conds.t3 = "mutated by generate()"  # upstream swaps .t3 in place
    mm.synthesize("standard", "b", ref_path=ref)
    assert model.conds.t3 == "t3"
    assert len(model.prepared) == 1


def test_cache_is_bounded(tmp_path):
    model = CondModel()
    mm = ModelManager(loader=lambda v, d: model)
    mm._cond_cache_max = 2
    for n in ("a", "b", "c"):
        mm.synthesize("standard", "x", ref_path=_ref(tmp_path, n))
    assert len(mm._cond_cache) == 2


def test_model_without_prepare_conditionals_keeps_old_path(tmp_path):
    class Plain:
        sr = 24000

        def generate(self, text, **kwargs):
            self.kwargs = kwargs
            return [0.0]

    m = Plain()
    ModelManager(loader=lambda v, d: m).synthesize("standard", "x", ref_path=_ref(tmp_path, "a"))
    assert m.kwargs["audio_prompt_path"].endswith("a.wav")
