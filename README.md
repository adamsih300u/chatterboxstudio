# Chatterbox Studio

A FastAPI + React front end over the unmodified [resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox) library.

- Text to speech, with the model variant (Turbo, Nano, standard, multilingual) chosen in the UI. Variants lazy-load on first use.
- Voice cloning from an uploaded reference clip, plus a saved voice library.
- OpenAI-compatible `POST /v1/audio/speech` (WAV output).

The backend installs the upstream library straight from GitHub, pinned to commit
`5de7a54aa4e5e2baadb0182dde554908b48b85c2` (v0.1.7). See `api/requirements.txt`.
To upgrade, change that SHA and rebuild.

## Run

    docker compose up --build

Open http://localhost:8000. Voices and the model cache persist in the `studio-data` volume
(`/data/voices`, `/data/hf`). Weights download from Hugging Face the first time a model loads.

## Models: prefetch and preload

The UI disables Generate until the selected model is loaded. An unloaded model shows a
"Load model" button, and the page polls until loading finishes.

Download weights ahead of time into the cache volume (one-off, nothing is baked into the image):

    docker compose run --rm chatterbox-studio python -m app.prefetch turbo nano standard multilingual

With no arguments every variant is fetched. Nano (110M, `ResembleAI/chatterbox-nano`) is the smallest
model and suits a Pi: it is upstream's `ChatterboxTurboTTS` loaded with `nano=True`, so it takes the
same settings and `[laugh]`-style tags as Turbo. Select it with `model: "nano"` on the API. To also load models into memory at startup, set
`PRELOAD_VARIANTS` (comma-separated, default `turbo` in `docker-compose.yml`). Each loaded
model uses RAM, so keep the list short on a Pi.

Offline host: download the Hugging Face snapshots elsewhere and copy them into `/data/hf`.

## OpenAI client

    from openai import OpenAI
    client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")
    audio = client.audio.speech.create(model="turbo", voice="my-saved-voice", input="Hello there.")
    audio.write_to_file("out.wav")

`voice` is the id of a saved voice; omit it or use `default` for the stock voice. Only `wav` is supported.

## Develop

    cd api && pip install -r requirements.txt -r requirements-dev.txt && pytest
    cd web && npm install && npm run dev   # proxies /api and /v1 to localhost:8000

## Build for arm64

    docker buildx build --platform linux/arm64 -t chatterbox-studio:arm64 .

CPU-only torch is used. No secrets live in this repo.
