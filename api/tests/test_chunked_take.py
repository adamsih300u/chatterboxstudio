import io
import wave


def _frames(content: bytes) -> int:
    with wave.open(io.BytesIO(content)) as w:
        return w.getnframes()


def test_whole_take_generates_one_sentence_per_call(client, engine):
    text = "The first sentence is right here. The second one follows it closely. And a third to finish."
    r = client.post("/api/tts", json={"model": "turbo", "text": text, "seed": 7})
    assert r.status_code == 200
    assert [c["text"] for c in engine.calls] == [
        "The first sentence is right here.",
        "The second one follows it closely.",
        "And a third to finish.",
    ]
    assert {c["seed"] for c in engine.calls} == {7}
    # 3 clips of 2400 frames plus two 150 ms sentence gaps at 24 kHz
    assert _frames(r.content) == 3 * 2400 + 2 * 3600


def test_long_first_sentence_is_not_cut_at_a_comma(client, engine):
    text = "One two three four, five six seven eight nine ten eleven twelve thirteen fourteen fifteen."
    client.post("/api/tts", json={"model": "turbo", "text": text})
    assert [c["text"] for c in engine.calls] == [text]
