"""Audio format conversion through ffmpeg: encode finished WAVs for export, decode uploads.

WAV stays the stored and generated format. Everything else is produced on request.
"""
import io
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Longest ringtone iOS accepts.
RINGTONE_MAX_SECONDS = 40.0
DECODE_RATE = 24000
FFMPEG_TIMEOUT = 300


class ConvertError(ValueError):
    pass


@dataclass(frozen=True)
class Format:
    ext: str
    mime: str
    args: tuple[str, ...]  # ffmpeg output arguments


FORMATS: dict[str, Format] = {
    "wav": Format("wav", "audio/wav", ("-c:a", "pcm_s16le", "-f", "wav")),
    "mp3": Format("mp3", "audio/mpeg", ("-c:a", "libmp3lame", "-q:a", "2", "-f", "mp3")),
    "m4a": Format("m4a", "audio/mp4", ("-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-f", "ipod")),
    "m4r": Format("m4r", "audio/x-m4r", ("-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-f", "ipod")),
    "flac": Format("flac", "audio/flac", ("-c:a", "flac", "-f", "flac")),
    "opus": Format("opus", "audio/ogg", ("-c:a", "libopus", "-b:a", "64k", "-f", "ogg")),
    "aac": Format("aac", "audio/aac", ("-c:a", "aac", "-b:a", "128k", "-f", "adts")),
    # Raw 16-bit little-endian mono PCM, as OpenAI's "pcm".
    "pcm": Format("pcm", "audio/L16", ("-c:a", "pcm_s16le", "-f", "s16le")),
}

# Formats the web UI offers for downloads.
EXPORT_FORMATS = ("wav", "mp3", "m4a", "m4r")


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def get_format(name: str) -> Format:
    fmt = FORMATS.get((name or "wav").lower())
    if fmt is None:
        raise ConvertError(f"unsupported format '{name}'; use one of {', '.join(FORMATS)}")
    return fmt


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _fail(proc: subprocess.CompletedProcess) -> ConvertError:
    tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
    return ConvertError(f"ffmpeg failed: {tail[0]}")


def _ffmpeg(args_before_input: list[str], data: bytes, args_after_input: list[str], out_ext: str) -> bytes:
    """Run ffmpeg between two temp files. MP4 and M4A/M4B need seekable input and output
    (their index is often at the end), so pipes will not do."""
    if not ffmpeg_available():
        raise ConvertError("ffmpeg is not installed on the server")
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in", Path(tmp) / f"out.{out_ext}"
        src.write_bytes(data)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args_before_input, "-i", str(src), *args_after_input, str(dst)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise ConvertError("audio conversion timed out")
        if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
            raise _fail(proc)
        return dst.read_bytes()


def encode(wav: bytes, name: str) -> tuple[bytes, Format]:
    """Re-encode WAV bytes. WAV in, WAV out is returned untouched."""
    fmt = get_format(name)
    if fmt.ext == "wav":
        return wav, fmt
    after = ["-vn", "-map_metadata", "-1"]
    if fmt.ext == "m4r" and wav_duration(wav) > RINGTONE_MAX_SECONDS:
        after += ["-t", str(RINGTONE_MAX_SECONDS)]
    return _ffmpeg([], wav, after + list(fmt.args), fmt.ext), fmt


def decode(data: bytes, start: float = 0.0, seconds: Optional[float] = None) -> bytes:
    """Any audio ffmpeg can read to 24 kHz mono 16-bit WAV, optionally only a window of it."""
    before = ["-ss", f"{start:.3f}"] if start > 0 else []
    after = ["-vn"]
    if seconds:
        after += ["-t", f"{seconds:.3f}"]
    after += ["-ac", "1", "-ar", str(DECODE_RATE), "-c:a", "pcm_s16le", "-f", "wav"]
    return _ffmpeg(before, data, after, "wav")


@lru_cache(maxsize=512)
def _probe(path: str, mtime: float) -> Optional[float]:
    if shutil.which("ffprobe") is None:
        return None
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        capture_output=True, timeout=30,
    )
    try:
        return float(proc.stdout.decode().strip())
    except ValueError:
        return None


def file_duration(path: Path) -> Optional[float]:
    """Length in seconds from the file header (WAV) or ffprobe (anything else). None if unknown."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except (wave.Error, EOFError):
            pass
    return _probe(str(path), mtime)
