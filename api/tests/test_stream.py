
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
