import io
import wave

import numpy as np
import pytest

from app.audio import cut_at_fraction, stitch
from app.script import Cut, parse_script, parse_script_cuts, parse_script_plan


def _frames(content: bytes) -> int:
    with wave.open(io.BytesIO(content)) as w:
        return w.getnframes()


def _voice(client, name):
    r = client.post("/api/voices", data={"name": name}, files={"file": (f"{name}.wav", name.encode())})
    assert r.status_code == 201


def test_cut_marks_previous_segment_and_keeps_voice():
    segs, cuts = parse_script_cuts("{Alice} I was saying {cut} {Bob} No you weren't. {cut}", None)
    assert segs == [("I was saying", "Alice"), ("No you weren't.", "Bob")]
    assert cuts == {0}


def test_cut_without_text_before_it_is_ignored():
    assert parse_script_cuts("{cut}{cut} Hello", None) == ([("Hello", None)], set())
    assert parse_script("{cut} A {cut} {Bob} B", None) == [("A", None), ("B", "Bob")]


def test_mid_line_cut_keeps_full_text_and_records_share():
    segs, cuts = parse_script_plan("{Tom} We never agreed to {cut} that deal. {Ann} Ha.", None)
    assert segs == [("We never agreed to that deal.", "Tom"), ("Ha.", "Ann")]
    assert set(cuts) == {0} and cuts[0].overlap_ms == 0
    spoken, rest = "We never agreed to", "that deal."
    n = lambda s: len("".join(s.split()))  # noqa: E731
    assert cuts[0].keep == pytest.approx(n(spoken) / (n(spoken) + n(rest)))


def test_cut_overlap_syntax():
    segs, cuts = parse_script_plan("{A} One two {cut 250} {B} Three", None)
    assert cuts == {0: Cut(None, 250)}
    assert parse_script_plan("A {cut 99999} B", None)[1][0].overlap_ms == 2000
    # a line-end cut after the last segment has nothing to cut into
    assert parse_script_plan("{A} Hi {cut 200}", None)[1] == {}


def _burst(n_loud, n_quiet=0):
    return np.concatenate([np.full(n_loud, 0.5, dtype=np.float32), np.zeros(n_quiet, dtype=np.float32)])


def test_cut_at_fraction_snaps_to_gap_and_fades():
    rate = 24000
    # two words of 0.5 s with a 60 ms gap, preceded by silence
    word = np.full(rate // 2, 0.5, dtype=np.float32)
    clip = np.concatenate([np.zeros(2000, np.float32), word, np.zeros(1440, np.float32), word, np.zeros(3000, np.float32)])
    out = cut_at_fraction(clip, rate, 0.45)  # guess lands a little before the gap
    gap_start, gap_end = 2000 + 12000, 2000 + 12000 + 1440
    assert gap_start - 480 <= len(out) <= gap_end + 480
    assert abs(out[-1]) < 0.01  # faded out
    assert out[0] == 0.0 and out.max() == pytest.approx(0.5)


def test_cut_at_fraction_on_silence_is_unchanged():
    silent = np.zeros(2400, dtype=np.float32)
    assert len(cut_at_fraction(silent, 24000, 0.5)) == 2400


def test_stitch_overlap_mixes_next_over_tail():
    rate = 1000
    first = _burst(1000, 500)
    second = np.concatenate([np.zeros(300, np.float32), _burst(1000)])
    out = stitch([first, second], rate, 100, {0}, {0: 200})
    # first keeps 1000 + 20 ms tail; second loses its lead except 10 ms, overlaps 200 ms
    assert len(out) == 1020 + (1010 - 200)
    assert out[815] == pytest.approx(0.5)  # first speaker alone
    assert out[900] == pytest.approx(1.0)  # both speaking in the overlap
    assert out[1019] == pytest.approx(0.5)  # second speaker alone


def test_mid_line_cut_via_api(client, engine):
    r = client.post("/api/tts", json={"model": "standard", "text": "I told you {cut 100} nothing at all. Fine."})
    assert r.status_code == 200
    assert [c["text"] for c in engine.calls] == ["I told you nothing at all. Fine."]


def test_stitch_cut_skips_gap_and_trims_tail():
    loud = np.concatenate([np.full(1000, 0.5, dtype=np.float32), np.zeros(5000, dtype=np.float32)])
    other = np.full(100, 0.5, dtype=np.float32)
    plain = stitch([loud, other], 24000, 100)
    cut = stitch([loud, other], 24000, 100, {0})
    assert len(plain) == 6000 + 2400 + 100
    assert len(cut) == 1000 + 480 + 100  # 20 ms tail kept, no gap


def test_cut_via_api_has_no_pause(client, engine):
    _voice(client, "Alice")
    r = client.post(
        "/api/tts",
        json={"model": "standard", "text": "One {cut} {Alice} Two. {default} Three.", "pause_ms": 100},
    )
    assert r.status_code == 200
    assert [c["text"] for c in engine.calls] == ["One", "Two.", "Three."]
    # three 2400-frame clips, one 100 ms gap (after "Two."), none after the cut
    assert _frames(r.content) == 3 * 2400 + 2400
