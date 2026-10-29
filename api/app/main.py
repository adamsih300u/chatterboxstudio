import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

from .audio import cut_at_fraction, stitch, to_wav_bytes
from .config import get_settings
from .db import Database
from .generations import GenerationStore
from .legacy import import_legacy
from .models import PARAM_SPECS, VARIANTS, ModelManager, clean_params, configure_threads, resolve_variant
from .script import parse_script_plan
from .voices import VoiceError, VoiceNotFound, VoiceStore

NO_VOICE = {"", "default", "none"}
MAX_SEGMENTS = 50
MAX_REGIONS = 100


VoiceRef = Field(default=None, max_length=64)


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
    id: Literal["turbo", "standard", "multilingual"]
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

    def render(
        model: Optional[str],
        segments: list[tuple[str, Optional[str]]],
        language: str,
        params: dict,
        pause_ms: int = 300,
        cuts: Optional[dict] = None,
    ) -> tuple[bytes, str, list[dict]]:
        """Generate every segment, stitch them, and return (wav, variant, resolved segments).

        Everything is validated before the first (slow) generation starts.
        """
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
        cuts = cuts or {}
        chunks, rate = [], 0
        for i, item in enumerate(plan):
            samples, rate = engine.synthesize(
                variant, item["text"], ref_path=item["ref"], language=language, params=clean
            )
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
    def tts(req: TTSRequest):
        if req.segments:
            segs, cuts = [(s.text, s.voice or req.voice) for s in req.segments], {}
        else:
            segs, cuts = parse_script_plan(req.text, req.voice)
        wav, variant, plan = render(req.model, segs, req.language, req.params, req.pause_ms, cuts)
        headers = {}
        if req.save:
            if len(plan) == 1:
                script = plan[0]["text"]
            else:
                script = "\n".join(f"{p['voice'] or 'Default'}: {p['text']}" for p in plan)
            names = list(dict.fromkeys(p["voice"] for p in plan if p["voice"]))
            clean = clean_params(variant, req.params)
            gen = gen_store.add(
                script, variant, ", ".join(names), req.language, wav, segments=plan, params=clean
            )
            headers["X-Generation-Id"] = gen.id
        return Response(wav, media_type="audio/wav", headers=headers)

    @app.post("/v1/audio/speech")
    def speech(req: SpeechRequest):
        if req.response_format != "wav":
            raise HTTPException(400, "only response_format 'wav' is supported")
        segs, cuts = parse_script_plan(req.input, req.voice)
        wav, _, _ = render(req.model, segs, req.language, req.params, cuts=cuts)
        return Response(wav, media_type="audio/wav")

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

    async def read_upload(upload: UploadFile) -> bytes:
        data = await upload.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "upload too large")
        return data

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
        data = await read_upload(file)
        orig = await read_upload(original) if original else None
        try:
            return store.add(
                name, data, file.filename or "", original=orig,
                original_filename=(original.filename or "") if original else "", regions=keep,
            ).public()
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
        data = await read_upload(file)
        orig = await read_upload(original) if original else None
        try:
            return store.update(
                voice_id, data, file.filename or "", regions=keep, original=orig,
                original_filename=(original.filename or "") if original else "",
            ).public()
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
    def voice_export(voice_id: str):
        """The trimmed clip as a download; upload it to another instance to import the voice."""
        voice = find_voice(voice_id)
        return FileResponse(voice.path, filename=f"{voice.id}{voice.path.suffix}")

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
            return Response(cached.read_bytes(), media_type="audio/wav", headers={"X-Preview-Cached": "true"})
        wav, _, _ = render(variant, [(settings.preview_text, voice.id)], req.language, req.params)
        store.save_preview(voice, key, wav)
        return Response(wav, media_type="audio/wav", headers={"X-Preview-Cached": "false"})

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
    def generation_audio(gen_id: str):
        gen = gen_store.get(gen_id)
        if gen is None:
            raise HTTPException(404, "generation not found")
        return FileResponse(gen.path, media_type="audio/wav")

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
