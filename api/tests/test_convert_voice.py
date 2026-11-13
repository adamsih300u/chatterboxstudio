import io
import wave
from types import SimpleNamespace

import pytest

from app.models import ModelManager


def wav(seconds=1.0, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def fake_decode(monkeypatch):
    """ffmpeg is not needed here: the 'decoded' audio is the upload itself, cut to the window."""
    seen = {}

    def decode(data, start=0.0, seconds=None):
        seen["window"] = seconds
        return data

    monkeypatch.setattr("app.main.decode", decode)
    return seen


@pytest.fixture
def alice(client):
    r = client.post("/api/voices", data={"name": "Alice"}, files={"file": ("a.wav", wav(), "audio/wav")})
    assert r.status_code == 201
    client.put(
        "/api/voices/alice/moods/angry", data={"regions": "[]"}, files={"file": ("angry.wav", wav(), "audio/wav")}
    )
    return r.json()


def post(client, **data):
    data.setdefault("lead_in_ms", "0")  # most tests assert exact durations
    return client.post("/api/convert-voice", data=data, files={"file": ("me.wav", wav(2.0), "audio/wav")})


def test_converts_into_a_saved_voice(client, engine, alice):
    r = post(client, voice="alice", seed="7")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.headers["x-seed"] == "7"
    assert float(r.headers["x-audio-duration"]) == pytest.approx(0.2)
    call = engine.conversions[0]
    assert call["ref"].is_file()
    assert call["seed"] == 7


def plain_client(engine, store, gen_store, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(engine=engine, store=store, gen_store=gen_store, static_dir=tmp_path / "no-ui"))


def test_lead_in_defaults_to_150ms_and_is_silent(engine, store, gen_store, tmp_path):
    client = plain_client(engine, store, gen_store, tmp_path)
    files = {"file": ("me.wav", wav(2.0), "audio/wav")}
    plain = client.post("/api/convert-voice", files=files)
    none = client.post("/api/convert-voice", data={"lead_in_ms": "0"}, files=files)
    assert float(plain.headers["x-audio-duration"]) == pytest.approx(0.2 + 0.15)
    assert float(none.headers["x-audio-duration"]) == pytest.approx(0.2)
    with wave.open(io.BytesIO(plain.content)) as w:
        assert w.readframes(3600) == b"\x00\x00" * 3600


def test_lead_in_is_range_checked(client):
    assert post(client, lead_in_ms="5000").status_code == 422
    assert post(client, lead_in_ms="-1").status_code == 422


def test_mood_picks_that_moods_clip(client, engine, alice):
    post(client, voice="alice")
    post(client, voice="alice", mood="angry")
    neutral, angry = (c["ref"] for c in engine.conversions)
    assert neutral != angry


def test_no_voice_uses_the_builtin_one(client, engine):
    assert post(client).status_code == 200
    assert engine.conversions[0]["ref"] is None
    assert post(client, voice="default").status_code == 200


def test_picks_a_random_seed_when_none_given(client, engine):
    r = post(client)
    assert r.headers["x-seed"].isdigit()
    assert engine.conversions[0]["seed"] == int(r.headers["x-seed"])


def test_unknown_voice_or_mood_is_404_before_converting(client, engine, alice):
    assert post(client, voice="nobody").status_code == 404
    assert post(client, voice="alice", mood="whisper").status_code == 404
    assert getattr(engine, "conversions", []) == []


def test_mood_without_voice_is_400(client):
    assert post(client, mood="angry").status_code == 400


def app_with_limit(monkeypatch, engine, store, gen_store, tmp_path, seconds):
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setenv("MAX_CONVERT_SECONDS", str(seconds))  # settings are read when the app is built
    return TestClient(create_app(engine=engine, store=store, gen_store=gen_store, static_dir=tmp_path / "no-ui"))


def test_over_long_recording_is_refused(monkeypatch, engine, store, gen_store, tmp_path):
    client = app_with_limit(monkeypatch, engine, store, gen_store, tmp_path, 1)
    assert post(client).status_code == 413  # the upload is 2 seconds
    assert getattr(engine, "conversions", []) == []


def test_decodes_a_window_just_past_the_limit(monkeypatch, engine, store, gen_store, tmp_path, fake_decode):
    client = app_with_limit(monkeypatch, engine, store, gen_store, tmp_path, 5)
    post(client)
    assert fake_decode["window"] == 6


def test_undecodable_audio_is_400(client, monkeypatch):
    from app.convert import ConvertError

    def boom(*a, **k):
        raise ConvertError("ffmpeg failed: invalid data")

    monkeypatch.setattr("app.main.decode", boom)
    assert post(client).status_code == 400


# ---- model manager ----


class VCModel:
    """Mimics ChatterboxVC: set_target_voice() sets ref_dict, generate() reads it."""

    sr = 24000

    def __init__(self):
        self.ref_dict = {"voice": "builtin"}
        self.embedded = []
        self.used = []

    def set_target_voice(self, path):
        self.embedded.append(path)
        self.ref_dict = {"voice": open(path, "rb").read().decode()}

    def generate(self, audio, target_voice_path=None):
        assert target_voice_path is None
        self.used.append(self.ref_dict["voice"])
        return SimpleNamespace(squeeze=lambda: [0.0] * 5)


def ref(tmp_path, name):
    p = tmp_path / f"{name}.wav"
    p.write_bytes(name.encode())
    return p


def manager(model):
    return ModelManager(loader=None, vc_loader=lambda device: model)


def test_loads_lazily_and_once(tmp_path):
    loads = []
    mm = ModelManager(vc_loader=lambda d: loads.append(d) or VCModel())
    assert loads == []
    mm.convert(tmp_path / "in.wav")
    mm.convert(tmp_path / "in.wav")
    assert loads == ["cpu"]


def test_reference_embedded_once_and_builtin_restored(tmp_path):
    model = VCModel()
    mm = manager(model)
    alice, bob = ref(tmp_path, "alice"), ref(tmp_path, "bob")
    for r in (alice, None, bob, alice, alice):
        mm.convert(tmp_path / "in.wav", ref_path=r)
    assert model.used == ["alice", "builtin", "bob", "alice", "alice"]
    assert len(model.embedded) == 2


def test_changed_clip_is_embedded_again(tmp_path):
    model = VCModel()
    mm = manager(model)
    r = ref(tmp_path, "alice")
    mm.convert(tmp_path / "in.wav", ref_path=r)
    r.write_bytes(b"alice, re-recorded")
    mm.convert(tmp_path / "in.wav", ref_path=r)
    assert model.used == ["alice", "alice, re-recorded"]


def test_reference_cache_is_bounded(tmp_path):
    mm = manager(VCModel())
    mm._vc_refs_max = 2
    for n in "abc":
        mm.convert(tmp_path / "in.wav", ref_path=ref(tmp_path, n))
    assert len(mm._vc_refs) == 2


def test_missing_builtin_voice_is_a_value_error(tmp_path):
    model = VCModel()
    model.ref_dict = None
    with pytest.raises(ValueError, match="built-in voice"):
        manager(model).convert(tmp_path / "in.wav")
