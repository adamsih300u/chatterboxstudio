from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .audio import cut_at_fraction, stitch, to_wav_bytes
from .config import get_settings
from .generations import GenerationStore
from .models import PARAM_SPECS, VARIANTS, ModelManager, clean_params, configure_threads, resolve_variant
from .script import parse_script_plan
from .voices import VoiceError, VoiceStore

NO_VOICE = {"", "default", "none"}
MAX_SEGMENTS = 50


class Segment(BaseModel):
    text: str
    voice: Optional[str] = None


class TTSRequest(BaseModel):
    text: str = ""
    # Explicit multi-voice list (alternative to inline {voice} tags in `text`):
    # each segment is generated on its own, then joined.
    # A segment without a voice falls back to the top-level `voice`.
    segments: Optional[list[Segment]] = None
    model: Optional[str] = None
    voice: Optional[str] = None
    language: str = "en"
    params: dict[str, Any] = {}
    pause_ms: int = 300
    save: bool = False


class DefaultVoiceRequest(BaseModel):
    id: Optional[str] = None


class BuiltinRequest(BaseModel):
    hidden: bool


class SpeechRequest(BaseModel):
    model: Optional[str] = None
    input: str
    voice: Optional[str] = None
    response_format: str = "wav"
    speed: float = 1.0
    language: str = "en"
    params: dict[str, Any] = {}


def create_app(engine=None, store=None, static_dir: Optional[Path] = None, gen_store=None) -> FastAPI:
    settings = get_settings()
    engine = engine or ModelManager()
    store = store or VoiceStore(settings.voices_dir)
    gen_store = gen_store or GenerationStore(settings.generations_dir)
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

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/models")
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

    @app.post("/api/models/{variant}/load", status_code=202)
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

    @app.get("/api/voices")
    def list_voices():
        # default None means the built-in voice is the default.
        return {
            "voices": [v.public() for v in store.list()],
            "default": store.default_id(),
            "builtin_hidden": store.builtin_hidden(),
        }

    @app.put("/api/voices/builtin")
    def set_builtin(req: BuiltinRequest):
        return {"builtin_hidden": store.set_builtin_hidden(req.hidden)}

    @app.put("/api/voices/default")
    def set_default_voice(req: DefaultVoiceRequest):
        try:
            return {"default": store.set_default(req.id)}
        except VoiceError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/voices", status_code=201)
    async def add_voice(name: str = Form(...), file: UploadFile = File(...)):
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "upload too large")
        try:
            return store.add(name, data, file.filename or "").public()
        except VoiceError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/voices/{voice_id}/audio")
    def voice_audio(voice_id: str):
        voice = store.get(voice_id)
        if voice is None:
            raise HTTPException(404, "voice not found")
        return FileResponse(voice.path)

    @app.delete("/api/voices/{voice_id}", status_code=204)
    def delete_voice(voice_id: str):
        if not store.delete(voice_id):
            raise HTTPException(404, "voice not found")

    @app.get("/api/generations")
    def list_generations():
        return {"generations": [g.public() for g in gen_store.list()]}

    @app.post("/api/generations", status_code=201)
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
