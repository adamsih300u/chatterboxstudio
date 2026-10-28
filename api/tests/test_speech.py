import io
import wave


def _assert_wav(content: bytes):
    with wave.open(io.BytesIO(content)) as w:
        assert w.getframerate() == 24000
        assert w.getnframes() == 2400


def test_openai_speech_default_voice(client, engine):
    r = client.post("/v1/audio/speech", json={"model": "chatterbox-turbo", "input": "hello", "voice": "alloy-less"})
    assert r.status_code == 404  # unknown voice names are not silently ignored
    r = client.post("/v1/audio/speech", json={"model": "chatterbox-turbo", "input": "hello", "voice": "default"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    _assert_wav(r.content)
    assert engine.calls[-1]["variant"] == "turbo" and engine.calls[-1]["ref"] is None


def test_speech_resolves_saved_voice(client, engine):
    client.post("/api/voices", data={"name": "Bob"}, files={"file": ("b.wav", b"abc")})
    r = client.post("/v1/audio/speech", json={"model": "chatterbox", "input": "hi", "voice": "bob"})
    assert r.status_code == 200
    assert engine.calls[-1]["variant"] == "standard"
    assert engine.calls[-1]["ref"].read_bytes() == b"abc"


def test_api_tts_multilingual_and_validation(client, engine):
    r = client.post("/api/tts", json={"text": "hola", "model": "multilingual", "language": "es"})
    assert r.status_code == 200
    assert engine.calls[-1]["language"] == "es"
    assert client.post("/api/tts", json={"text": "  "}).status_code == 400
    assert client.post("/api/tts", json={"text": "x", "model": "nope"}).status_code == 400
    bad = client.post("/v1/audio/speech", json={"input": "x", "response_format": "mp3"})
    assert bad.status_code == 400


def test_models_lazy_loaded_flag(client):
    before = {m["id"]: m["loaded"] for m in client.get("/api/models").json()["models"]}
    assert not any(before.values())
    client.post("/api/tts", json={"text": "x", "model": "standard"})
    after = {m["id"]: m["loaded"] for m in client.get("/api/models").json()["models"]}
    assert after["standard"] and not after["turbo"]
