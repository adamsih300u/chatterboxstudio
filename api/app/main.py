import asyncio
import hashlib
import itertools
import json
import secrets
import threading
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

from .audio import cut_at_fraction, pcm16_bytes, stitch, streaming_wav_header, to_wav_bytes
from .chunking import split_text
from .config import VariantName, get_settings
from .convert import ConvertError, decode, encode, get_format, wav_duration
from .db import Database
from .generations import GenerationStore
from .legacy import import_legacy
from .models import PARAM_SPECS, VARIANTS, ModelManager, clean_params, configure_threads, resolve_variant
from .script import parse_script_plan
from .voices import VoiceError, VoiceNotFound, VoiceStore

NO_VOICE = {"", "default", "none"}
MAX_SEED = 2**32 - 1
MAX_SEGMENTS = 50
SENTENCE_GAP_MS = 150  # silence between sentences of one line when streaming
MAX_REGIONS = 100


class Cancelled(Exception):
    """The client went away mid-take; stop and keep nothing."""


VoiceRef = Field(default=None, max_length=64)

# Uploads the model's audio loader may not read; they are converted to WAV when saved.
CONVERT_ON_UPLOAD = {".m4a", ".m4b", ".aac", ".opus"}
MAX_DECODE_SECONDS = 1800.0


def duration_headers(wav: bytes, headers: Optional[dict] = None) -> dict:
    """Response headers that tell the client how long the audio is before it plays."""
    out = dict(headers or {})
    try:
        out["X-Audio-Duration"] = f"{wav_duration(wav):.3f}"
    except Exception:
        pass
    return out


class Segment(BaseModel):
    text: str
    voice: Optional[str] = VoiceRef


class TTSRequest(BaseModel):
    # Total text length is capped by settings.max_text_chars (MAX_TEXT_CHARS) in render().
    text: str = ""
    # Explicit multi-voice list (alternative to inline {voice} tags in `text`):
    # each segment is generated on its own, then joined.
    # A segment without a voice falls back to the top-level `voice`.
    segments: Optional[list[Segment]] = Field(default=None, max_length=MAX_SEGMENTS)
    model: Optional[str] = Field(default=None, max_length=64)
    voice: Optional[str] = VoiceRef
    language: str = Field(default="en", min_length=2, max_length=16)
    params: dict[str, Any] = {}
    pause_ms: int = Field(default=300, ge=0, le=5000)
    save: bool = False
    # Same seed + text + voice + settings gives the same take. Omitted: a random one is picked
    # and returned in the X-Seed header (and stored with the History entry).
    seed: Optional[int] = Field(default=None, ge=0, le=MAX_SEED)


class DefaultVoiceRequest(BaseModel):
    id: Optional[str] = Field(default=None, max_length=64)


class BuiltinRequest(BaseModel):
    hidden: bool


class SpeechRequest(BaseModel):
    model: Optional[str] = Field(default=None, max_length=64)
    input: str
    voice: Optional[str] = VoiceRef
    response_format: str = "wav"  # checked in the route so clients get a 400 with a clear message
    speed: float = Field(default=1.0, gt=0, le=4)
    language: str = Field(default="en", min_length=2, max_length=16)
    params: dict[str, Any] = {}
    seed: Optional[int] = Field(default=None, ge=0, le=MAX_SEED)


class Region(BaseModel):
    """A keep-region of a voice's original upload, in seconds."""

    start: float = Field(ge=0)
    end: float = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self):
        if self.end <= self.start:
            raise ValueError("region end must be after its start")
        return self


REGIONS = TypeAdapter(list[Region])


def parse_regions(raw: str) -> list[dict]:
    try:
        regions = REGIONS.validate_json(raw or "[]")
    except ValidationError as e:
        raise HTTPException(400, f"invalid regions: {e.errors()[0]['msg']}")
    if len(regions) > MAX_REGIONS:
        raise HTTPException(400, f"at most {MAX_REGIONS} regions")
    return [r.model_dump() for r in regions]


class PreviewRequest(BaseModel):
    model: Optional[str] = Field(default=None, max_length=64)
    language: str = Field(default="en", min_length=2, max_length=16)
    params: dict[str, Any] = {}


class VoiceOut(BaseModel):
    id: str
    name: str
    created: float
    updated: float
    regions: list[Region]
    has_original: bool
    duration: Optional[float] = None  # seconds of the saved clip, when it can be read


class VoiceList(BaseModel):
    voices: list[VoiceOut]
    default: Optional[str]  # None means the built-in voice is the default
    builtin_hidden: bool


class DefaultVoiceOut(BaseModel):
    default: Optional[str]


class BuiltinOut(BaseModel):
    builtin_hidden: bool


class SegmentOut(BaseModel):
    text: str
    voice: str = ""


class GenerationOut(BaseModel):
    id: str
    text: str
    model: str
    voice: str
    language: str
    created: float
    segments: list[SegmentOut]
    params: dict[str, Any]
    seed: Optional[int] = None  # None for clips saved before seeds existed
    duration: Optional[float] = None  # seconds


class GenerationList(BaseModel):
    generations: list[GenerationOut]


class ParamSpec(BaseModel):
    label: str
    type: Literal["float", "int", "bool"]
    default: Any
    help: str
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None


class ModelInfo(BaseModel):
    id: VariantName
    label: str
    multilingual: bool
    state: Literal["idle", "loading", "ready", "error"]
    loaded: bool
    error: Optional[str]
    params: dict[str, ParamSpec]


class ModelList(BaseModel):
    default: str
    models: list[ModelInfo]


class LoadStarted(BaseModel):
    id: str
    state: str


class Health(BaseModel):
    status: Literal["ok"]


def create_app(engine=None, store=None, static_dir: Optional[Path] = None, gen_store=None) -> FastAPI:
    settings = get_settings()
    engine = engine or ModelManager()
    if store is None or gen_store is None:
        db = Database(settings.database_path)
        import_legacy(db, settings.voices_dir, settings.generations_dir)
        store = store or VoiceStore(settings.voices_dir, db)
        gen_store = gen_store or GenerationStore(settings.generations_dir, db)
    static_dir = static_dir if static_dir is not None else settings.static_dir
    app = FastAPI(title="Chatterbox Studio")

    def prepare(
        model: Optional[str], segments: list[tuple[str, Optional[str]]], params: dict
    ) -> tuple[str, dict, list[dict]]:
        """Validate a request and resolve it to (variant, clean params, plan). Raises before any generation."""
        if not segments or any(not text.strip() for text, _ in segments):
            raise HTTPException(400, "text is empty")
        if len(segments) > MAX_SEGMENTS:
            raise HTTPException(400, f"at most {MAX_SEGMENTS} segments per request")
        if sum(len(text) for text, _ in segments) > settings.max_text_chars:
            raise HTTPException(413, f"text is longer than {settings.max_text_chars} characters")
        try:
            variant = resolve_variant(model, settings.default_variant)
            clean = clean_params(variant, params)
        except ValueError as e:
            raise HTTPException(400, str(e))
        plan = []
        for text, voice in segments:
            ref, label = None, ""
            if voice and voice.lower() not in NO_VOICE:
                found = store.get(voice)
                if found is None:
                    raise HTTPException(404, f"voice '{voice}' not found")
                ref, label = found.path, getattr(found, "name", voice)
            plan.append({"text": text, "voice": label, "ref": ref})
        return variant, clean, plan

    def render(
        model: Optional[str],
        segments: list[tuple[str, Optional[str]]],
        language: str,
        params: dict,
        pause_ms: int = 300,
        cuts: Optional[dict] = None,
        seed: Optional[int] = None,
        by_sentence: bool = True,
        cancel: Optional[threading.Event] = None,
    ) -> tuple[bytes, str, list[dict]]:
        """Generate every segment, stitch them, and return (wav, variant, resolved segments).

        Everything is validated before the first (slow) generation starts.
        """
        variant, clean, plan = prepare(model, segments, params)
        cuts = cuts or {}
        chunks, rate = [], 0
        for i, item in enumerate(plan):
            # One sentence per call, so a bad draw ruins a sentence and not the whole line.
            parts = []
            pieces = split_text(item["text"], cut_first=False) if by_sentence else []
            for piece in pieces or [item["text"]]:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                piece_samples, rate = engine.synthesize(
                    variant, piece, ref_path=item["ref"], language=language, params=clean, seed=seed
                )
                piece_samples = np.asarray(piece_samples, dtype=np.float32).reshape(-1)
                if parts:
                    parts.append(np.zeros(int(rate * SENTENCE_GAP_MS / 1000), dtype=np.float32))
                parts.append(piece_samples)
            samples = parts[0] if len(parts) == 1 else np.concatenate(parts)
            cut = cuts.get(i)
            if cut is not None and cut.keep is not None:
                samples = cut_at_fraction(samples, rate, cut.keep)
            chunks.append(samples)
        overlaps = {i: c.overlap_ms for i, c in cuts.items() if c.overlap_ms}
        wav = to_wav_bytes(stitch(chunks, rate, min(max(pause_ms, 0), 5000), set(cuts), overlaps), rate)
        return wav, variant, [{"text": i["text"], "voice": i["voice"]} for i in plan]

    @app.get("/api/health", response_model=Health)
    def health():
        return {"status": "ok"}

    @app.get("/api/models", response_model=ModelList)
    def models():
        status = engine.status()
        return {
            "default": settings.default_variant,
            "models": [
                {
                    "id": key,
                    "label": v["label"],
                    "multilingual": v["multilingual"],
                    "state": status[key]["state"],
                    "loaded": status[key]["state"] == "ready",
                    "error": status[key]["error"],
                    "params": PARAM_SPECS[key],
                }
                for key, v in VARIANTS.items()
            ],
        }

    @app.post("/api/models/{variant}/load", status_code=202, response_model=LoadStarted)
    def load_model(variant: str):
        try:
            return {"id": variant, "state": engine.start_load(variant)}
        except ValueError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/tts")
    async def tts(req: TTSRequest, request: Request):
        if req.segments:
            segs, cuts = [(s.text, s.voice or req.voice) for s in req.segments], {}
        else:
            segs, cuts = parse_script_plan(req.text, req.voice)
        seed = req.seed if req.seed is not None else secrets.randbelow(MAX_SEED + 1)
        # A closed connection (the Stop button) cancels the take: generation stops at the next
        # sentence and nothing is saved to History, even if most of it was already done.
        cancel = threading.Event()

        def work():
            wav, variant, plan = render(req.model, segs, req.language, req.params, req.pause_ms, cuts, seed, cancel=cancel)
            headers = duration_headers(wav, {"X-Seed": str(seed)})
            if req.save:
                if cancel.is_set():
                    raise Cancelled()
                headers["X-Generation-Id"] = save_take(req, variant, plan, wav, seed)
            return Response(wav, media_type="audio/wav", headers=headers)

        async def watch():
            while not cancel.is_set():
                if await request.is_disconnected():
                    cancel.set()
                    return
                await asyncio.sleep(0.2)

        watcher = asyncio.create_task(watch())
        try:
            return await run_in_threadpool(work)
        except Cancelled:
            return Response(status_code=499)
        finally:
            watcher.cancel()

    def save_take(req: TTSRequest, variant: str, plan: list[dict], wav: bytes, seed: int) -> str:
        if len(plan) == 1:
            script = plan[0]["text"]
        else:
            script = "\n".join(f"{p['voice'] or 'Default'}: {p['text']}" for p in plan)
        names = list(dict.fromkeys(p["voice"] for p in plan if p["voice"]))
        gen = gen_store.add(
            script, variant, ", ".join(names), req.language, wav,
            segments=plan, params=clean_params(variant, req.params), seed=seed,
        )
        return gen.id

    @app.post("/api/tts/stream")
    def tts_stream(req: TTSRequest):
        """Same request as /api/tts, answered as a WAV that arrives sentence by sentence.

        Scripts with {cut} are refused: a cut line is only known once it is fully generated
        and the next line may overlap it. The take is saved to History when the stream finishes.
        """
        if req.segments:
            segs, cuts = [(s.text, s.voice or req.voice) for s in req.segments], {}
        else:
            segs, cuts = parse_script_plan(req.text, req.voice)
        if cuts:
            raise HTTPException(400, "scripts with {cut} can't be streamed; use /api/tts")
        seed = req.seed if req.seed is not None else secrets.randbelow(MAX_SEED + 1)
        variant, clean, plan = prepare(req.model, segs, req.params)
        pause = min(max(req.pause_ms, 0), 5000)

        def body():
            kept, rate, started = [], 0, False
            for si, item in enumerate(plan):
                pieces = split_text(item["text"]) or [item["text"]]
                for pi, piece in enumerate(pieces):
                    samples, rate = engine.synthesize(
                        variant, piece, ref_path=item["ref"], language=req.language, params=clean, seed=seed
                    )
                    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
                    gap_ms = pause if (pi == 0 and si > 0) else (SENTENCE_GAP_MS if pi > 0 else 0)
                    if gap_ms:
                        samples = np.concatenate([np.zeros(int(rate * gap_ms / 1000), dtype=np.float32), samples])
                    kept.append(samples)
                    if not started:
                        started = True
                        yield streaming_wav_header(rate)
                    yield pcm16_bytes(samples)
            # Only reached when the client stayed to the end.
            if req.save and kept:
                save_take(req, variant, plan, to_wav_bytes(np.concatenate(kept), rate), seed)

        stream = body()
        first = next(stream)  # a failure before any audio is a normal error response
        first += next(stream)  # header and first sentence go out together
        return StreamingResponse(
            itertools.chain([first], stream), media_type="audio/wav", headers={"X-Seed": str(seed)}
        )

    @app.post("/v1/audio/speech")
    def speech(req: SpeechRequest):
        try:
            get_format(req.response_format)
        except ConvertError as e:
            raise HTTPException(400, str(e))
        segs, cuts = parse_script_plan(req.input, req.voice)
        seed = req.seed if req.seed is not None else secrets.randbelow(MAX_SEED + 1)
        wav, _, _ = render(req.model, segs, req.language, req.params, cuts=cuts, seed=seed)
        data, fmt = encode_or_400(wav, req.response_format)
        return Response(data, media_type=fmt.mime, headers={"X-Seed": str(seed)})

    def encode_or_400(wav: bytes, name: str):
        try:
            return encode(wav, name)
        except ConvertError as e:
            raise HTTPException(400, str(e))

    def attachment(base: str, fmt) -> dict:
        return {"Content-Disposition": f'attachment; filename="{base}.{fmt.ext}"'}

    @app.post("/api/convert")
    async def convert(file: UploadFile = File(...), format: str = "mp3"):
        """Re-encode a WAV (a fresh, unsaved generation) for download."""
        wav = await read_upload(file)
        try:
            wav_duration(wav)
        except Exception:
            raise HTTPException(400, "expected a WAV file")
        data, fmt = encode_or_400(wav, format)
        return Response(data, media_type=fmt.mime, headers=attachment("speech", fmt))

    @app.post("/api/audio/decode")
    async def decode_audio(file: UploadFile = File(...), start: float = 0.0, seconds: float = MAX_DECODE_SECONDS):
        """Any audio ffmpeg reads (M4B audiobooks included) as a mono WAV window, so the browser
        never has to decode a whole book."""
        if start < 0 or seconds <= 0:
            raise HTTPException(400, "start must be >= 0 and seconds > 0")
        data = await read_upload(file, settings.max_decode_bytes)
        try:
            wav = decode(data, start, min(seconds, MAX_DECODE_SECONDS))
        except ConvertError as e:
            raise HTTPException(400, str(e))
        return Response(wav, media_type="audio/wav", headers=duration_headers(wav))

    @app.get("/api/voices", response_model=VoiceList)
    def list_voices():
        # default None means the built-in voice is the default.
        return {
            "voices": [v.public() for v in store.list()],
            "default": store.default_id(),
            "builtin_hidden": store.builtin_hidden(),
        }

    @app.put("/api/voices/builtin", response_model=BuiltinOut)
    def set_builtin(req: BuiltinRequest):
        return {"builtin_hidden": store.set_builtin_hidden(req.hidden)}

    @app.put("/api/voices/default", response_model=DefaultVoiceOut)
    def set_default_voice(req: DefaultVoiceRequest):
        try:
            return {"default": store.set_default(req.id)}
        except VoiceError as e:
            raise HTTPException(404, str(e))

    async def read_upload(upload: UploadFile, limit: Optional[int] = None) -> bytes:
        limit = limit or settings.max_upload_bytes
        data = await upload.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, "upload too large")
        return data

    def normalize(data: bytes, filename: str) -> tuple[bytes, str]:
        """M4A, M4B and friends are stored as WAV so every later step reads them the same way."""
        if Path(filename or "").suffix.lower() not in CONVERT_ON_UPLOAD:
            return data, filename
        try:
            return decode(data, 0.0, MAX_DECODE_SECONDS), Path(filename).stem + ".wav"
        except ConvertError as e:
            raise HTTPException(400, f"could not read {filename}: {e}")

    @app.post("/api/voices", status_code=201, response_model=VoiceOut)
    async def add_voice(
        name: str = Form(..., min_length=1, max_length=64),
        file: UploadFile = File(...),
        original: Optional[UploadFile] = File(None),
        regions: str = Form("[]"),
    ):
        """`file` is the clip used for cloning. `original` (the untrimmed upload) and `regions`
        (how it was cut) are optional and let the clip be re-trimmed later."""
        keep = parse_regions(regions)
        data, fname = normalize(await read_upload(file), file.filename or "")
        orig, oname = (None, "")
        if original:
            orig, oname = normalize(await read_upload(original), original.filename or "")
        try:
            return store.add(name, data, fname, original=orig, original_filename=oname, regions=keep).public()
        except VoiceError as e:
            raise HTTPException(400, str(e))

    @app.put("/api/voices/{voice_id}", response_model=VoiceOut)
    async def update_voice(
        voice_id: str,
        file: UploadFile = File(...),
        original: Optional[UploadFile] = File(None),
        regions: str = Form("[]"),
    ):
        """Replace a voice's clip, keeping its name. Previews of the old clip are discarded."""
        keep = parse_regions(regions)
        data, fname = normalize(await read_upload(file), file.filename or "")
        orig, oname = (None, "")
        if original:
            orig, oname = normalize(await read_upload(original), original.filename or "")
        try:
            return store.update(voice_id, data, fname, regions=keep, original=orig, original_filename=oname).public()
        except VoiceNotFound as e:
            raise HTTPException(404, str(e))
        except VoiceError as e:
            raise HTTPException(400, str(e))

    def find_voice(voice_id: str):
        voice = store.get(voice_id)
        if voice is None:
            raise HTTPException(404, "voice not found")
        return voice

    @app.get("/api/voices/{voice_id}/audio")
    def voice_audio(voice_id: str):
        return FileResponse(find_voice(voice_id).path)

    @app.get("/api/voices/{voice_id}/source")
    def voice_source(voice_id: str):
        """The untrimmed upload for re-trimming (the clip itself for voices saved without one)."""
        return FileResponse(find_voice(voice_id).source_path)

    @app.get("/api/voices/{voice_id}/export")
    def voice_export(voice_id: str, format: str = ""):
        """The trimmed clip as a download; upload it to another instance to import the voice.
        Without `format` the stored file is sent as is."""
        voice = find_voice(voice_id)
        if not format:
            return FileResponse(voice.path, filename=f"{voice.id}{voice.path.suffix}")
        data, fmt = encode_or_400(to_wav_file(voice.path), format)
        return Response(data, media_type=fmt.mime, headers=attachment(voice.id, fmt))

    def to_wav_file(path: Path) -> bytes:
        data = path.read_bytes()
        if path.suffix.lower() == ".wav":
            return data
        try:
            return decode(data)
        except ConvertError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/voices/{voice_id}/preview")
    def voice_preview(voice_id: str, req: PreviewRequest):
        """Speak a fixed sentence in this voice. Cached until the clip, model, language,
        parameters or sentence change."""
        voice = find_voice(voice_id)
        try:
            variant = resolve_variant(req.model, settings.default_variant)
            clean = clean_params(variant, req.params)
        except ValueError as e:
            raise HTTPException(400, str(e))
        language = req.language if VARIANTS[variant]["multilingual"] else ""
        key = hashlib.sha256(
            json.dumps(
                [store.clip_digest(voice), variant, language, clean, settings.preview_text],
                sort_keys=True, default=str,
            ).encode()
        ).hexdigest()[:32]
        cached = store.preview_path(voice, key)
        if cached.is_file():
            cached_wav = cached.read_bytes()
            return Response(cached_wav, media_type="audio/wav", headers=duration_headers(cached_wav, {"X-Preview-Cached": "true"}))
        wav, _, _ = render(
            variant, [(settings.preview_text, voice.id)], req.language, req.params, by_sentence=False
        )
        store.save_preview(voice, key, wav)
        return Response(wav, media_type="audio/wav", headers=duration_headers(wav, {"X-Preview-Cached": "false"}))

    @app.delete("/api/voices/{voice_id}", status_code=204)
    def delete_voice(voice_id: str):
        if not store.delete(voice_id):
            raise HTTPException(404, "voice not found")

    @app.get("/api/generations", response_model=GenerationList)
    def list_generations():
        return {"generations": [g.public() for g in gen_store.list()]}

    @app.post("/api/generations", status_code=201, response_model=GenerationOut)
    async def add_generation(
        text: str = Form(...),
        model: str = Form(""),
        voice: str = Form(""),
        language: str = Form("en"),
        file: UploadFile = File(...),
    ):
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "upload too large")
        try:
            return gen_store.add(text, model, voice, language, data).public()
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/generations/{gen_id}/audio")
    def generation_audio(gen_id: str, format: str = "wav", download: bool = False):
        gen = gen_store.get(gen_id)
        if gen is None:
            raise HTTPException(404, "generation not found")
        if format.lower() == "wav":
            return FileResponse(gen.path, media_type="audio/wav", filename=f"{gen.id}.wav" if download else None)
        data, fmt = encode_or_400(gen.path.read_bytes(), format)
        return Response(data, media_type=fmt.mime, headers=attachment(gen.id, fmt))

    @app.delete("/api/generations/{gen_id}", status_code=204)
    def delete_generation(gen_id: str):
        if not gen_store.delete(gen_id):
            raise HTTPException(404, "generation not found")

    # Mounted last so it never shadows the API routes.
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    return app


def app_factory() -> FastAPI:
    configure_threads()
    engine = ModelManager(warmup=True)
    engine.preload(list(get_settings().preload_variants))
    return create_app(engine=engine)
