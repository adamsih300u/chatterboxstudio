"""Model manager: lazily loads one stock chatterbox model per variant."""
import copy
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

log = logging.getLogger(__name__)

# turbo_family: loaded through upstream's ChatterboxTurboTTS (Nano is the same class with nano=True),
# which takes Turbo's generate() arguments and conditionals API.
VARIANTS = {
    "turbo": {"label": "Turbo", "multilingual": False, "turbo_family": True},
    "nano": {"label": "Nano", "multilingual": False, "turbo_family": True},
    "standard": {"label": "Standard", "multilingual": False, "turbo_family": False},
    "multilingual": {"label": "Multilingual", "multilingual": True, "turbo_family": False},
}

ALIASES = {
    "turbo": "turbo",
    "chatterbox-turbo": "turbo",
    "nano": "nano",
    "chatterbox-nano": "nano",
    "standard": "standard",
    "chatterbox": "standard",
    "multilingual": "multilingual",
    "chatterbox-multilingual": "multilingual",
}


def _p(label, lo, hi, default, step, help, kind="float"):
    return {"label": label, "type": kind, "min": lo, "max": hi, "default": default, "step": step, "help": help}


_TEMP = _p("Temperature", 0.05, 5.0, 0.8, 0.05, "Higher is more varied and less stable.")
_REP = _p("Repetition penalty", 1.0, 2.0, 1.2, 0.05, "Higher discourages repeated sounds and words.")


def _classic() -> dict:
    return {
        "exaggeration": _p("Exaggeration", 0.25, 2.0, 0.5, 0.05, "Emotion intensity. Around 0.7 and up gets dramatic and can speed speech up."),
        "cfg_weight": _p("CFG weight / pace", 0.0, 1.0, 0.5, 0.05, "Lower values slow the pace. Try 0 when the reference voice has a different accent."),
        "temperature": _TEMP,
        "repetition_penalty": _REP,
        "min_p": _p("min_p", 0.0, 1.0, 0.05, 0.01, "Drops very unlikely tokens."),
        "top_p": _p("top_p", 0.0, 1.0, 1.0, 0.01, "Nucleus sampling cutoff. 1.0 disables it."),
    }


# Per-variant generate() arguments, taken from the stock library signatures.
# Turbo and Nano ignore exaggeration and cfg_weight upstream, so they are not offered.
def _turbo_family() -> dict:
    return {
        "temperature": _TEMP,
        "repetition_penalty": _REP,
        "top_k": _p("top_k", 1, 1000, 1000, 1, "Sample from only the k most likely tokens.", "int"),
        "top_p": _p("top_p", 0.0, 1.0, 0.95, 0.01, "Nucleus sampling cutoff."),
        "min_p": _p("min_p", 0.0, 1.0, 0.0, 0.01, "Drops very unlikely tokens."),
        "norm_loudness": {"label": "Normalize loudness", "type": "bool", "default": True, "help": "Level the output volume."},
    }


PARAM_SPECS = {
    "turbo": _turbo_family(),
    "nano": _turbo_family(),
    "standard": _classic(),
    "multilingual": _classic(),
}


def clean_params(variant: str, params: Optional[dict]) -> dict:
    """Validate generate() overrides for a variant. Raises ValueError on bad input."""
    spec = PARAM_SPECS[variant]
    out: dict[str, Any] = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        if key not in spec:
            raise ValueError(f"parameter '{key}' is not supported by {variant}")
        s = spec[key]
        if s["type"] == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"parameter '{key}' must be true or false")
            out[key] = value
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"parameter '{key}' must be a number")
        value = int(value) if s["type"] == "int" else float(value)
        if not s["min"] <= value <= s["max"]:
            raise ValueError(f"parameter '{key}' must be between {s['min']} and {s['max']}")
        out[key] = value
    return out


def resolve_variant(name: Optional[str], default: str = "turbo") -> str:
    """Map a UI variant or OpenAI-style model name to a variant key.

    Generic OpenAI names (tts-1, tts-1-hd) and empty values map to the default.
    Raises ValueError for anything else.
    """
    if not name or name in ("tts-1", "tts-1-hd"):
        return default
    try:
        return ALIASES[name.lower()]
    except KeyError:
        raise ValueError(f"unknown model '{name}'")


def configure_threads() -> Optional[int]:
    """Size torch's CPU thread pool to the cores this container may use.

    TORCH_THREADS overrides the detected count (useful on SMT hosts, where the
    detected count is logical cores). Returns the thread count, or None when
    torch is not installed. Sampling and output are unaffected.
    """
    try:
        import torch
    except ImportError:
        return None
    env = os.environ.get("TORCH_THREADS", "").strip()
    if env.isdigit() and int(env) > 0:
        n = int(env)
    else:
        try:
            n = len(os.sched_getaffinity(0))  # honors docker --cpuset-cpus
        except AttributeError:
            n = os.cpu_count() or 1
    torch.set_num_threads(n)
    log.info("torch intra-op threads set to %d", n)
    return n


def _seed_torch(seed: int) -> None:
    try:
        import torch
    except ImportError:
        return
    torch.manual_seed(seed)


def _load_variant(variant: str, device: str) -> Any:
    # Imported lazily so the API (and its tests) start without torch loaded.
    if variant == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        return ChatterboxTurboTTS.from_pretrained(device=device)
    if variant == "nano":
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        return ChatterboxTurboTTS.from_pretrained(device=device, nano=True)
    if variant == "standard":
        from chatterbox.tts import ChatterboxTTS

        return ChatterboxTTS.from_pretrained(device=device)
    if variant == "multilingual":
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        return ChatterboxMultilingualTTS.from_pretrained(device=device)
    raise ValueError(f"unknown variant '{variant}'")


class ModelManager:
    def __init__(
        self,
        loader: Optional[Callable[[str, str], Any]] = None,
        device: str = "cpu",
        warmup: bool = False,
    ):
        self._loader = loader or _load_variant
        self._device = device
        self._warmup = warmup
        self._models: dict[str, Any] = {}
        self._load_lock = threading.Lock()
        self._gen_lock = threading.Lock()  # one CPU inference at a time
        self._state_lock = threading.Lock()
        self._state: dict[str, dict[str, Optional[str]]] = {}
        # Reference-voice embeddings built by upstream's prepare_conditionals(),
        # reused so a saved voice is processed once, not on every request.
        self._cond_cache: OrderedDict[tuple, Any] = OrderedDict()
        self._cond_cache_max = 16
        self._default_conds: dict[str, Any] = {}  # each model's built-in voice

    def loaded(self) -> list[str]:
        return sorted(self._models)

    def _set_state(self, variant: str, state: str, error: Optional[str] = None) -> None:
        with self._state_lock:
            self._state[variant] = {"state": state, "error": error}

    def status(self) -> dict[str, dict[str, Optional[str]]]:
        """Per-variant state: idle, loading (includes queued), ready or error."""
        with self._state_lock:
            idle = {"state": "idle", "error": None}
            return {v: dict(self._state.get(v, idle)) for v in VARIANTS}

    def start_load(self, variant: str) -> str:
        """Begin loading one variant in the background. Returns the resulting state."""
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant '{variant}'")
        with self._state_lock:
            current = self._state.get(variant, {}).get("state", "idle")
            if current in ("loading", "ready"):
                return current
            self._state[variant] = {"state": "loading", "error": None}
        threading.Thread(target=self._load_quietly, args=([variant],), daemon=True).start()
        return "loading"

    def preload(self, variants: list[str]) -> None:
        """Load variants one after another in a background thread (startup use)."""
        todo = [v for v in variants if v in VARIANTS and v not in self._models]
        if not todo:
            return
        for v in todo:
            self._set_state(v, "loading")
        threading.Thread(target=self._load_quietly, args=(todo,), daemon=True).start()

    def _load_quietly(self, variants: list[str]) -> None:
        for v in variants:
            try:
                self.get(v)
            except Exception:
                pass  # recorded as the variant's error state by get()

    def get(self, variant: str) -> Any:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant '{variant}'")
        with self._load_lock:
            if variant not in self._models:
                self._set_state(variant, "loading")
                try:
                    self._models[variant] = self._loader(variant, self._device)
                except Exception as e:
                    self._set_state(variant, "error", str(e))
                    raise
                self._default_conds[variant] = getattr(self._models[variant], "conds", None)
                if self._warmup:
                    self._run_warmup(variant, self._models[variant])
                self._set_state(variant, "ready")
            return self._models[variant]

    def _run_warmup(self, variant: str, model: Any) -> None:
        """One short throwaway generation so the first real request is not the cold one."""
        kwargs: dict[str, Any] = {}
        if VARIANTS[variant]["multilingual"]:
            kwargs["language_id"] = "en"
        try:
            with self._gen_lock:
                model.generate("Warming up.", **kwargs)
        except Exception:
            log.exception("warm-up generation failed for %s", variant)  # not fatal

    def _select_conditionals(self, variant: str, model: Any, ref_path: Optional[Path], kwargs: dict) -> None:
        """Point model.conds at the right voice. Caller holds the generation lock.

        Upstream's generate() rebuilds the reference embeddings on every call that
        has a clip, and leaves them set afterwards, so a later no-voice request
        would inherit them. Here each voice is prepared once by upstream's own
        prepare_conditionals() and cached, and no-voice requests get the built-in
        voice back. generate() still applies exaggeration itself, so output matches.
        """
        if ref_path is None:
            default = self._default_conds.get(variant)
            if default is not None:
                model.conds = default
            return
        turbo = VARIANTS[variant]["turbo_family"]
        norm = kwargs.get("norm_loudness", True) if turbo else None
        st = Path(ref_path).stat()
        key = (variant, str(ref_path), st.st_mtime_ns, st.st_size, norm)
        cached = self._cond_cache.get(key)
        if cached is None:
            prep: dict[str, Any] = {"exaggeration": kwargs.get("exaggeration", 0.0 if turbo else 0.5)}
            if turbo:
                prep["norm_loudness"] = norm
            model.prepare_conditionals(str(ref_path), **prep)
            cached = model.conds
            self._cond_cache[key] = cached
            while len(self._cond_cache) > self._cond_cache_max:
                self._cond_cache.popitem(last=False)
        else:
            self._cond_cache.move_to_end(key)
        model.conds = copy.copy(cached)  # generate() may swap .t3 for a new exaggeration

    def synthesize(
        self,
        variant: str,
        text: str,
        ref_path: Optional[Path] = None,
        language: str = "en",
        params: Optional[dict] = None,
        seed: Optional[int] = None,
    ) -> tuple[np.ndarray, int]:
        """Generate one clip. A seed makes the sampling repeatable for the same text, voice and settings."""
        model = self.get(variant)
        kwargs: dict[str, Any] = dict(params or {})
        if VARIANTS[variant]["multilingual"]:
            kwargs["language_id"] = language
        with self._gen_lock:
            if hasattr(model, "prepare_conditionals"):
                self._select_conditionals(variant, model, ref_path, kwargs)
            elif ref_path is not None:
                kwargs["audio_prompt_path"] = str(ref_path)
            if seed is not None:
                _seed_torch(seed)
            wav = model.generate(text, **kwargs)
        samples = wav.squeeze().detach().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav)
        return samples, int(model.sr)
