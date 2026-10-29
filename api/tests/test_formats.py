"""Audio formats: MP3/M4A/M4R/... exports, M4A/M4B uploads, and durations in the API."""
import numpy as np
import pytest

from app import convert
from app.audio import to_wav_bytes

needs_ffmpeg = pytest.mark.skipif(not convert.ffmpeg_available(), reason="ffmpeg is not installed")


def tone(seconds: float, rate: int = 24000) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    return to_wav_bytes((0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), rate)


def as_m4a(wav: bytes, ext: str = "m4a") -> bytes:
    return convert.encode(wav, "m4a")[0]


# ---- no ffmpeg needed ----


def test_unknown_format_is_rejected_before_generating(client, engine):
    r = client.post("/v1/audio/speech", json={"input": "hi", "response_format": "midi"})
    assert r.status_code == 400
    assert "unsupported format" in r.json()["detail"]
    assert engine.calls == []


def test_wav_speech_unchanged(client):
    r = client.post("/v1/audio/speech", json={"input": "hi", "response_format": "wav"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"


def test_tts_reports_duration_header(client):
    r = client.post("/api/tts", json={"text": "hi"})
    assert float(r.headers["x-audio-duration"]) == pytest.approx(0.1, abs=0.01)


def test_voice_and_generation_lists_carry_duration(client):
    client.post("/api/voices", data={"name": "ann"}, files={"file": ("a.wav", tone(2.0), "audio/wav")})
    voices = client.get("/api/voices").json()["voices"]
    assert voices[0]["duration"] == pytest.approx(2.0, abs=0.01)

    client.post("/api/tts", json={"text": "hi", "save": True})
    gens = client.get("/api/generations").json()["generations"]
    assert gens[0]["duration"] == pytest.approx(0.1, abs=0.01)


def test_m4r_ringtone_limit_constant():
    assert convert.RINGTONE_MAX_SECONDS == 40.0


def test_generation_audio_rejects_unknown_format(client):
    client.post("/api/tts", json={"text": "hi", "save": True})
    gid = client.get("/api/generations").json()["generations"][0]["id"]
    assert client.get(f"/api/generations/{gid}/audio?format=midi").status_code == 400


# ---- needs ffmpeg ----


@needs_ffmpeg
@pytest.mark.parametrize(
    "fmt,mime,magic",
    [("mp3", "audio/mpeg", None), ("m4a", "audio/mp4", b"ftyp"), ("m4r", "audio/x-m4r", b"ftyp"), ("flac", "audio/flac", b"fLaC"), ("opus", "audio/ogg", b"OggS")],
)
def test_speech_formats(client, fmt, mime, magic):
    r = client.post("/v1/audio/speech", json={"input": "hi", "response_format": fmt})
    assert r.status_code == 200
    assert r.headers["content-type"] == mime
    assert len(r.content) > 100
    if magic:
        assert magic in r.content[:16]
    else:  # MP3 starts with an ID3 tag or a frame sync
        assert r.content[:3] == b"ID3" or r.content[0] == 0xFF


@needs_ffmpeg
def test_m4r_is_cut_to_forty_seconds():
    data, fmt = convert.encode(tone(60.0, 16000), "m4r")
    back = convert.decode(data)
    assert fmt.ext == "m4r"
    assert convert.wav_duration(back) == pytest.approx(40.0, abs=0.5)


@needs_ffmpeg
def test_decode_window():
    m4a = as_m4a(tone(10.0))
    whole = convert.decode(m4a)
    window = convert.decode(m4a, start=2.0, seconds=3.0)
    assert convert.wav_duration(whole) == pytest.approx(10.0, abs=0.2)
    assert convert.wav_duration(window) == pytest.approx(3.0, abs=0.2)


@needs_ffmpeg
def test_decode_route_takes_m4b(client):
    m4b = as_m4a(tone(5.0))
    r = client.post("/api/audio/decode?seconds=2", files={"file": ("book.m4b", m4b, "audio/mp4")})
    assert r.status_code == 200
    assert float(r.headers["x-audio-duration"]) == pytest.approx(2.0, abs=0.2)
    assert r.content[:4] == b"RIFF"


@needs_ffmpeg
def test_decode_route_rejects_garbage(client):
    r = client.post("/api/audio/decode", files={"file": ("x.m4b", b"not audio", "audio/mp4")})
    assert r.status_code == 400


@needs_ffmpeg
def test_m4a_upload_is_stored_as_wav(client, store):
    r = client.post("/api/voices", data={"name": "narrator"}, files={"file": ("clip.m4a", as_m4a(tone(3.0)), "audio/mp4")})
    assert r.status_code == 201
    assert r.json()["duration"] == pytest.approx(3.0, abs=0.2)
    assert store.get("narrator").path.suffix == ".wav"


@needs_ffmpeg
def test_voice_export_and_generation_download_in_other_formats(client):
    client.post("/api/voices", data={"name": "ann"}, files={"file": ("a.wav", tone(2.0), "audio/wav")})
    r = client.get("/api/voices/ann/export?format=mp3")
    assert r.status_code == 200
    assert 'filename="ann.mp3"' in r.headers["content-disposition"]

    client.post("/api/tts", json={"text": "hi", "save": True})
    gid = client.get("/api/generations").json()["generations"][0]["id"]
    r = client.get(f"/api/generations/{gid}/audio?format=m4a")
    assert r.headers["content-type"] == "audio/mp4"
    assert f'filename="{gid}.m4a"' in r.headers["content-disposition"]


@needs_ffmpeg
def test_convert_route(client):
    r = client.post("/api/convert?format=m4r", files={"file": ("speech.wav", tone(1.0), "audio/wav")})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/x-m4r"
    assert client.post("/api/convert?format=mp3", files={"file": ("x.wav", b"nope")}).status_code == 400
