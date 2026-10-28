import io
import wave
from typing import Optional

import numpy as np

SILENCE_LEVEL = 0.01
CUT_TAIL_MS = 20
SNAP_WINDOW_MS = 150
FADE_OUT_MS = 30
LEAD_KEEP_MS = 10


def _loud_bounds(samples: np.ndarray) -> Optional[tuple[int, int]]:
    loud = np.flatnonzero(np.abs(samples) > SILENCE_LEVEL)
    if loud.size == 0:
        return None
    return int(loud[0]), int(loud[-1]) + 1


def trim_tail(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Drop trailing near-silence, keeping a few ms so the cut does not click."""
    bounds = _loud_bounds(samples)
    if bounds is None:
        return samples
    return samples[: min(len(samples), bounds[1] + int(sample_rate * CUT_TAIL_MS / 1000))]


def trim_head(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Drop leading near-silence, keeping a few ms so the onset does not click."""
    bounds = _loud_bounds(samples)
    if bounds is None:
        return samples
    return samples[max(0, bounds[0] - int(sample_rate * LEAD_KEEP_MS / 1000)) :]


def cut_at_fraction(samples: np.ndarray, sample_rate: int, keep: float) -> np.ndarray:
    """Keep the first `keep` share of the speech and fade it out.

    The model gives no word timings, so the position is a character-share guess
    inside the spoken part of the clip. It then snaps to the quietest spot nearby,
    which is usually a gap between words.
    """
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    bounds = _loud_bounds(samples)
    if bounds is None:
        return samples
    start, end = bounds
    guess = start + int((end - start) * min(max(keep, 0.0), 1.0))
    window = int(sample_rate * SNAP_WINDOW_MS / 1000)
    lo, hi = max(start, guess - window), min(end, guess + window)
    frame = max(1, int(sample_rate * 0.01))
    if hi - lo > frame:
        env = np.convolve(np.abs(samples[lo:hi]), np.ones(frame, dtype=np.float32) / frame, mode="valid")
        centers = lo + frame // 2 + np.arange(env.size)
        scale = float(env.max()) or 1.0
        score = env / scale + 0.3 * np.abs(centers - guess) / window
        guess = int(centers[int(np.argmin(score))])
    out = samples[:guess].copy()
    fade = min(len(out), int(sample_rate * FADE_OUT_MS / 1000))
    if fade:
        out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return out


def stitch(
    chunks: list,
    sample_rate: int,
    pause_ms: int = 0,
    cuts: Optional[set] = None,
    overlaps: Optional[dict] = None,
) -> np.ndarray:
    """Join mono chunks end to end with pause_ms of silence between them.

    Chunks whose index is in `cuts` lose their trailing silence and get no pause
    after them, so the next chunk starts immediately. If `overlaps` maps a cut
    chunk to N ms, the next chunk also loses its leading silence and is mixed in
    over the last N ms of the cut one, like someone talking over them.
    """
    cuts = cuts or set()
    overlaps = overlaps or {}
    flat = [np.asarray(c, dtype=np.float32).reshape(-1) for c in chunks]
    if len(flat) == 1:
        return flat[0]
    gap = np.zeros(int(sample_rate * max(pause_ms, 0) / 1000), dtype=np.float32)
    out = np.zeros(0, dtype=np.float32)
    for i, c in enumerate(flat):
        overlap = int(sample_rate * max(overlaps.get(i - 1, 0), 0) / 1000) if i else 0
        if i and (i - 1) in cuts and overlap:
            c = trim_head(c, sample_rate)
            n = min(overlap, len(out), len(c))
            if n:
                out = out.copy()
                out[-n:] += c[:n]
            c = c[n:]
        elif i and (i - 1) not in cuts:
            out = np.concatenate([out, gap])
        if i in cuts:
            c = trim_tail(c, sample_rate)
        out = np.concatenate([out, c])
    return out


def to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode mono float samples in [-1, 1] as 16-bit PCM WAV."""
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
