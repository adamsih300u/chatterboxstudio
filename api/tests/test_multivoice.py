import io
import wave


def _frames(content: bytes) -> int:
    with wave.open(io.BytesIO(content)) as w:
        return w.getnframes()


def _voice(client, name):
    r = client.post("/api/voices", data={"name": name}, files={"file": (f"{name}.wav", name.encode())})
    assert r.status_code == 201
    return r.json()["id"]


def test_segments_use_own_voice_and_are_stitched(client, engine):
    a, b = _voice(client, "Alice"), _voice(client, "Bob")
    r = client.post(
        "/api/tts",
        json={
            "model": "standard",
            "segments": [{"text": "one", "voice": a}, {"text": "two", "voice": b}, {"text": "three"}],
            "pause_ms": 100,
        },
    )
    assert r.status_code == 200
    assert [c["text"] for c in engine.calls] == ["one", "two", "three"]
    assert [c["ref"].read_bytes() if c["ref"] else None for c in engine.calls] == [b"Alice", b"Bob", None]
    # 3 clips of 2400 frames plus two 100 ms gaps at 24 kHz
    assert _frames(r.content) == 3 * 2400 + 2 * 2400


def test_inline_voice_tags_split_and_stitch(client, engine):
    _voice(client, "Alice"), _voice(client, "Bob Jones")
    r = client.post(
        "/api/tts",
        json={"model": "standard", "text": "Intro. {Alice} Hi there. {bob jones} Hello! {default} Bye.", "pause_ms": 100},
    )
    assert r.status_code == 200
    assert [c["text"] for c in engine.calls] == ["Intro.", "Hi there.", "Hello!", "Bye."]
    refs = [c["ref"].read_bytes() if c["ref"] else None for c in engine.calls]
    assert refs == [None, b"Alice", b"Bob Jones", None]
    assert _frames(r.content) == 4 * 2400 + 3 * 2400


def test_inline_tags_fall_back_to_chosen_voice_and_keep_sound_tags(client, engine):
    _voice(client, "Alice"), _voice(client, "Bob")
    client.post("/api/tts", json={"model": "turbo", "voice": "alice", "text": "Hi [laugh] there. {Bob} Yo."})
    assert [c["text"] for c in engine.calls[-2:]] == ["Hi [laugh] there.", "Yo."]
    assert [c["ref"].read_bytes() for c in engine.calls[-2:]] == [b"Alice", b"Bob"]


def test_inline_tag_unknown_voice_fails_before_generation(client, engine):
    r = client.post("/api/tts", json={"text": "Hello {ghost} there"})
    assert r.status_code == 404 and engine.calls == []
    assert client.post("/api/tts", json={"text": "{ghost}"}).status_code == 400


def test_openai_endpoint_supports_inline_tags(client, engine):
    _voice(client, "Alice")
    r = client.post("/v1/audio/speech", json={"input": "One. {Alice} Two.", "model": "chatterbox"})
    assert r.status_code == 200
    assert [c["text"] for c in engine.calls[-2:]] == ["One.", "Two."]


def test_segment_without_voice_uses_top_level_voice(client, engine):
    a = _voice(client, "Alice")
    client.post("/api/tts", json={"voice": a, "segments": [{"text": "x"}]})
    assert engine.calls[-1]["ref"].read_bytes() == b"Alice"


def test_bad_segment_fails_before_any_generation(client, engine):
    r = client.post("/api/tts", json={"segments": [{"text": "ok"}, {"text": "x", "voice": "ghost"}]})
    assert r.status_code == 404
    assert engine.calls == []
    r = client.post("/api/tts", json={"segments": [{"text": "ok"}, {"text": "  "}]})
    assert r.status_code == 400 and engine.calls == []


def test_params_validated_and_passed(client, engine):
    r = client.post("/api/tts", json={"text": "hi", "model": "standard", "params": {"exaggeration": 0.9, "cfg_weight": 0.2}})
    assert r.status_code == 200
    assert engine.calls[-1]["params"] == {"exaggeration": 0.9, "cfg_weight": 0.2}
    # turbo has top_k and norm_loudness but not exaggeration
    ok = client.post("/api/tts", json={"text": "hi", "model": "turbo", "params": {"top_k": 50, "norm_loudness": False}})
    assert ok.status_code == 200 and engine.calls[-1]["params"] == {"top_k": 50, "norm_loudness": False}
    for model, params in [
        ("turbo", {"exaggeration": 0.5}),
        ("standard", {"exaggeration": 9}),
        ("standard", {"temperature": "hot"}),
        ("turbo", {"norm_loudness": 1}),
    ]:
        assert client.post("/api/tts", json={"text": "x", "model": model, "params": params}).status_code == 400


def test_models_expose_param_specs(client):
    by_id = {m["id"]: m for m in client.get("/api/models").json()["models"]}
    assert "top_k" in by_id["turbo"]["params"] and "exaggeration" not in by_id["turbo"]["params"]
    assert by_id["standard"]["params"]["exaggeration"]["default"] == 0.5


def test_openai_endpoint_accepts_params(client, engine):
    r = client.post("/v1/audio/speech", json={"input": "hi", "model": "chatterbox", "params": {"temperature": 0.5}})
    assert r.status_code == 200 and engine.calls[-1]["params"] == {"temperature": 0.5}


def test_save_flag_stores_history_with_segments(client):
    a = _voice(client, "Alice")
    r = client.post(
        "/api/tts",
        json={"model": "standard", "save": True, "segments": [{"text": "hello", "voice": a}, {"text": "bye"}],
              "params": {"exaggeration": 0.7}},
    )
    gen_id = r.headers["x-generation-id"]
    gens = client.get("/api/generations").json()["generations"]
    assert [g["id"] for g in gens] == [gen_id]
    assert gens[0]["segments"][0] == {"text": "hello", "voice": "Alice"}
    assert gens[0]["params"] == {"exaggeration": 0.7}
    assert gens[0]["text"] == "Alice: hello\nDefault: bye"
    assert client.get(f"/api/generations/{gen_id}/audio").status_code == 200
    # without the flag nothing is stored
    client.post("/api/tts", json={"text": "x"})
    assert len(client.get("/api/generations").json()["generations"]) == 1
