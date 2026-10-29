import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_defaults(monkeypatch):
    for k in ("PRELOAD_VARIANTS", "DEFAULT_VARIANT", "MAX_UPLOAD_BYTES"):
        monkeypatch.delenv(k, raising=False)
    s = Settings()
    assert s.default_variant == "turbo" and s.preload_variants == ()


def test_settings_parse_preload_list(monkeypatch):
    monkeypatch.setenv("PRELOAD_VARIANTS", " turbo, standard ,")
    assert Settings().preload_variants == ("turbo", "standard")


@pytest.mark.parametrize(
    "name,value",
    [
        ("PRELOAD_VARIANTS", "turbo,bogus"),
        ("DEFAULT_VARIANT", "fast"),
        ("MAX_UPLOAD_BYTES", "lots"),
        ("MAX_UPLOAD_BYTES", "0"),
    ],
)
def test_settings_reject_bad_env(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "body",
    [
        {"text": "hi", "pause_ms": -1},
        {"text": "hi", "pause_ms": 99999},
        {"text": "hi", "language": "x"},
        {"text": "hi", "voice": "v" * 65},
        {"text": "hi", "segments": [{"text": "a"}] * 51},
    ],
)
def test_tts_rejects_out_of_range_fields(client, engine, body):
    assert client.post("/api/tts", json=body).status_code == 422
    assert engine.calls == []


def test_speech_rejects_bad_speed(client, engine):
    assert client.post("/v1/audio/speech", json={"input": "hi", "speed": 0}).status_code == 422
    assert client.post("/v1/audio/speech", json={"input": "hi", "speed": 9}).status_code == 422
    assert engine.calls == []


def test_text_length_cap(engine, store, gen_store, tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_TEXT_CHARS", "20")
    from fastapi.testclient import TestClient

    from app.main import create_app

    c = TestClient(create_app(engine=engine, store=store, gen_store=gen_store, static_dir=tmp_path / "no-ui"))
    assert c.post("/api/tts", json={"text": "x" * 20}).status_code == 200
    engine.calls.clear()
    r = c.post("/api/tts", json={"text": "x" * 21})
    assert r.status_code == 413 and engine.calls == []


def test_voice_name_length_limit(client):
    r = client.post("/api/voices", data={"name": "n" * 65}, files={"file": ("a.wav", b"RIFF", "audio/wav")})
    assert r.status_code == 422
    r = client.post("/api/voices", data={"name": ""}, files={"file": ("a.wav", b"RIFF", "audio/wav")})
    assert r.status_code == 422


def test_openapi_documents_variants(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    assert schema["ModelInfo"]["properties"]["id"]["enum"] == ["turbo", "standard", "multilingual"]
