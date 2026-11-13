
from app.chunking import split_text


def test_split_sentences_and_merge_short_ones():
    assert split_text("Hello there my friend. How are you doing today? Fine.") == [
        "Hello there my friend.",
        "How are you doing today? Fine.",
    ]


def test_long_first_sentence_is_cut_at_a_comma():
    text = "When the evening comes down over the quiet hills, the geese walk home across the wet grass."
    parts = split_text(text)
    assert parts[0] == "When the evening comes down over the quiet hills,"
    assert " ".join(parts) == text


def test_single_short_text_stays_whole():
    assert split_text("Yes.") == ["Yes."]
    assert split_text("   ") == []


def _read(body: bytes):
    # Streamed WAVs carry 0xFFFFFFFF sizes; read the PCM after the 44-byte header.
    assert body[:4] == b"RIFF" and body[8:12] == b"WAVE"
    return int.from_bytes(body[24:28], "little"), body[44:]


def test_stream_returns_wav_with_every_sentence(client, engine):
    r = client.post("/api/tts/stream", json={"text": "First sentence is right here. Second sentence follows it.", "seed": 5})
    assert r.status_code == 200
    assert r.headers["X-Seed"] == "5"
    rate, pcm = _read(r.content)
    assert rate == 24000
    assert [c["text"] for c in engine.calls] == ["First sentence is right here.", "Second sentence follows it."]
    assert all(c["seed"] == 5 for c in engine.calls)
    # two stub clips of 2400 samples plus one 150 ms gap, 2 bytes each
    assert len(pcm) == (2400 * 2 + 3600) * 2


def test_stream_pauses_between_voices(client, engine, store):
    r = client.post("/api/tts/stream", json={"text": "{default} Hello to you all. {default} Good night to you.", "pause_ms": 500})
    assert r.status_code == 200
    _, pcm = _read(r.content)
    assert len(pcm) >= (2400 * 2 + 12000) * 2 or len(engine.calls) == 1


def test_stream_refuses_cut_scripts(client):
    r = client.post("/api/tts/stream", json={"text": "Hello there my friend. {cut} Another line."})
    assert r.status_code == 400


def test_stream_validates_before_streaming(client):
    assert client.post("/api/tts/stream", json={"text": ""}).status_code == 400
    assert client.post("/api/tts/stream", json={"text": "Hi there.", "voice": "nobody"}).status_code == 404


def test_stream_saves_to_history(client, gen_store):
    r = client.post("/api/tts/stream", json={"text": "Save this take for later. And this one.", "save": True, "seed": 9})
    assert r.status_code == 200
    items = gen_store.list()
    assert len(items) == 1 and items[0].seed == 9
    assert items[0].text == "Save this take for later. And this one."


def test_stream_lead_in_is_added_once(client):
    body = {"text": "First sentence is right here. Second sentence follows it.", "lead_in_ms": 100}
    _, pcm = _read(client.post("/api/tts/stream", json=body).content)
    # two stub clips, one 150 ms sentence gap, one 100 ms lead-in
    assert len(pcm) == (2400 * 2 + 3600 + 2400) * 2
    assert pcm[:4800] == b"\x00" * 4800


def test_tts_default_lead_in_is_silence_before_the_take(client):
    import io, wave
    def frames(lead):
        r = client.post("/api/tts", json={"text": "Hello there friend.", "lead_in_ms": lead})
        assert r.status_code == 200
        with wave.open(io.BytesIO(r.content)) as w:
            return w.getnframes()
    assert frames(150) - frames(0) == 3600
    assert frames(150) > frames(0)


def test_server_default_lead_in_is_150ms(engine, store, gen_store, tmp_path):
    """Uses a plain client: the shared fixture defaults lead_in_ms to 0."""
    import io, wave
    from fastapi.testclient import TestClient
    from app.main import DEFAULT_LEAD_IN_MS, create_app

    plain = TestClient(create_app(engine=engine, store=store, gen_store=gen_store, static_dir=tmp_path / "no-ui"))

    def frames(body):
        r = plain.post("/api/tts", json={"text": "Hello there friend.", **body})
        with wave.open(io.BytesIO(r.content)) as w:
            return w.getnframes()

    assert DEFAULT_LEAD_IN_MS == 150
    assert frames({}) - frames({"lead_in_ms": 0}) == 3600
