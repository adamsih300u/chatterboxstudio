# syntax=docker/dockerfile:1

# ---- Stage 1: build the Vite UI ----
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- Stage 2: Python deps (git is needed for the pinned upstream install) ----
FROM python:3.11-slim AS deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ---- Stage 3: runtime ----
FROM python:3.11-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    VOICES_DIR=/data/voices \
    STATIC_DIR=/app/static \
    HF_HOME=/data/hf
WORKDIR /app
COPY api/app ./app
COPY --from=web /web/dist ./static
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
