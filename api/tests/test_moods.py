import io
import wave

import pytest

from app.voices import VoiceNotFound, resolve_spec


def wav(seconds=1.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def upload(name="a.wav"):
    return {"file": (name, wav(), "audio/wav")}


@pytest.fixture
def alice(client):
    r = client.post("/api/voices", data={"name": "Alice"}, files={**upload("calm.wav"), "original": ("orig.wav", wav(), "audio/wav")})
    assert r.status_code == 201
    return r.json()


def put_mood(client, name, regions="[]"):
    return client.put(f"/api/voices/alice/moods/{name}", data={"regions": regions}, files=upload(f"{name}.wav"))


def test_new_voice_lists_neutral_mood_and_source(alice):
    assert [m["id"] for m in alice["moods"]] == ["neutral"]
    assert [s["id"] for s in alice["sources"]] == ["s1"]


def test_put_mood_adds_and_lists_it(client, alice):
    r = put_mood(client, "Angry", '[{"source": "s1", "start": 0, "end": 0.5}]')
    assert r.status_code == 200
    moods = {m["id"]: m for m in r.json()["moods"]}
    assert set(moods) == {"neutral", "angry"}
    assert moods["angry"]["regions"] == [{"source": "s1", "start": 0.0, "end": 0.5}]


def test_put_mood_rejects_unknown_source_and_reserved_names(client, alice):
    assert put_mood(client, "angry", '[{"source": "s9", "start": 0, "end": 1}]').status_code == 400
    assert put_mood(client, "cut").status_code == 400
    assert client.put("/api/voices/nobody/moods/x", files=upload()).status_code == 404


def test_add_source_then_cut_a_mood_from_it(client, alice):
    r = client.post("/api/voices/alice/sources", files=upload("more.wav"))
    assert r.status_code == 201
    assert [s["id"] for s in r.json()["sources"]] == ["s1", "s2"]
    r = put_mood(client, "whisper", '[{"source": "s2", "start": 0, "end": 0.5}]')
    assert r.status_code == 200
    # An upload a mood is cut from can't be removed until that mood is re-cut or deleted.
    assert client.delete("/api/voices/alice/sources/s2").status_code == 400
    assert client.delete("/api/voices/alice/moods/whisper").status_code == 200
    assert client.delete("/api/voices/alice/sources/s2").status_code == 200


def test_neutral_mood_cannot_be_deleted(client, alice):
    assert client.delete("/api/voices/alice/moods/neutral").status_code == 400


def test_tag_in_single_voice_script_picks_mood(client, engine, alice):
    put_mood(client, "angry")
    r = client.post("/api/tts", json={"text": "Calm now. {angry} Furious now.", "voice": "Alice"})
    assert r.status_code == 200, r.text
    refs = [c["ref"].name for c in engine.calls]
    assert refs[0].startswith("ref") and refs[-1] == "mood-angry.wav"


def test_alice_tag_returns_to_neutral_and_colon_form_is_exact(client, engine, alice):
    put_mood(client, "angry")
    client.post("/api/voices", data={"name": "Bob"}, files=upload("bob.wav"))
    r = client.post("/api/tts", json={"text": "{Alice:angry} One. {Bob} Two. {Alice} Three."})
    assert r.status_code == 200, r.text
    refs = [(c["ref"].parent.name, c["ref"].name) for c in engine.calls]
    assert refs[0] == ("alice", "mood-angry.wav")
    assert refs[1][0] == "bob"
    assert refs[2][0] == "alice" and refs[2][1].startswith("ref")


def test_unknown_mood_is_a_404_before_any_generation(client, engine, alice):
    r = client.post("/api/tts", json={"text": "Hi. {Alice:nope} There.", "voice": "Alice"})
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]
    assert engine.calls == []


def test_bare_name_falls_back_to_a_voice_when_no_such_mood(client, engine, alice):
    client.post("/api/voices", data={"name": "Bob"}, files=upload("bob.wav"))
    r = client.post("/api/tts", json={"text": "Hi. {Bob} There.", "voice": "Alice"})
    assert r.status_code == 200
    assert engine.calls[-1]["ref"].parent.name == "bob"


def test_mood_preview_uses_the_mood_clip(client, engine, alice):
    put_mood(client, "angry")
    r = client.post("/api/voices/alice/preview", json={"mood": "angry"})
    assert r.status_code == 200
    assert engine.calls[-1]["ref"].name == "mood-angry.wav"
    assert client.post("/api/voices/alice/preview", json={"mood": "nope"}).status_code == 404


def test_mood_and_source_audio_routes(client, alice):
    put_mood(client, "angry")
    assert client.get("/api/voices/alice/moods/angry/audio").status_code == 200
    assert client.get("/api/voices/alice/sources/s1/audio").status_code == 200
    assert client.get("/api/voices/alice/moods/zzz/audio").status_code == 404


def test_resolve_spec_directly(store):
    store.add("Alice", wav(), "a.wav")
    alice = store.get("alice")
    voice, mood = resolve_spec(store, "alice", None)
    assert mood.id == "neutral"
    with pytest.raises(VoiceNotFound):
        resolve_spec(store, "ghost", alice)
