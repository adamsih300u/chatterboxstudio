import type { components } from "./api-types";

type Schemas = components["schemas"];

export type ModelInfo = Schemas["ModelInfo"];
export type ModelList = Schemas["ModelList"];
export type LoadStarted = Schemas["LoadStarted"];
export type VoiceOut = Schemas["VoiceOut"];
export type Region = Schemas["Region"];
export type VoiceList = Schemas["VoiceList"];
export type DefaultVoiceOut = Schemas["DefaultVoiceOut"];
export type BuiltinOut = Schemas["BuiltinOut"];
export type GenerationOut = Schemas["GenerationOut"];
export type GenerationList = Schemas["GenerationList"];
export type ParamSpec = Schemas["ParamSpec"];

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

async function check(res: Response): Promise<Response> {
  if (!res.ok) {
    let detail: string = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string" && body.detail) detail = body.detail;
    } catch {
      /* body was not JSON */
    }
    throw new Error(detail);
  }
  return res;
}

async function json<T>(res: Response): Promise<T> {
  return (await check(res)).json() as Promise<T>;
}

// The server states a clip's length up front, so it can be shown before playback.
function headerSeconds(res: Response): number | null {
  const v = parseFloat(res.headers.get("X-Audio-Duration") ?? "");
  return Number.isFinite(v) ? v : null;
}

export async function getModels(): Promise<ModelList> {
  return json(await fetch("/api/models"));
}

export async function loadModel(id: string): Promise<LoadStarted> {
  return json(await fetch(`/api/models/${encodeURIComponent(id)}/load`, { method: "POST" }));
}

export async function getVoices(): Promise<VoiceList> {
  return json(await fetch("/api/voices"));
}

export async function setDefaultVoice(id: string | null): Promise<DefaultVoiceOut> {
  return json(
    await fetch("/api/voices/default", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id || null }),
    })
  );
}

export async function setBuiltinHidden(hidden: boolean): Promise<BuiltinOut> {
  return json(
    await fetch("/api/voices/builtin", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hidden }),
    })
  );
}

// `file` is the clip used for cloning. Pass the untrimmed upload as `original` and the cut as
// `regions` to keep the voice re-trimmable.
export async function addVoice(name: string, file: Blob, original?: File, regions: Region[] = []): Promise<VoiceOut> {
  const body = new FormData();
  body.append("name", name);
  body.append("file", file);
  if (original) body.append("original", original);
  body.append("regions", JSON.stringify(regions));
  return json(await fetch("/api/voices", { method: "POST", body }));
}

// Replace a voice's clip. The server keeps the stored original unless one is given.
export async function updateVoice(id: string, file: Blob, regions: Region[]): Promise<VoiceOut> {
  const body = new FormData();
  body.append("file", file);
  body.append("regions", JSON.stringify(regions));
  return json(await fetch(`/api/voices/${encodeURIComponent(id)}`, { method: "PUT", body }));
}

// The untrimmed upload (or the clip itself for voices saved without one).
export async function getVoiceSource(id: string): Promise<Blob> {
  return (await check(await fetch(`/api/voices/${encodeURIComponent(id)}/source`))).blob();
}

export function voiceExportUrl(id: string, format = "wav"): string {
  return `/api/voices/${encodeURIComponent(id)}/export?format=${encodeURIComponent(format)}`;
}

export function generationAudioUrl(id: string, format = "wav", download = false): string {
  const q = `format=${encodeURIComponent(format)}${download ? "&download=true" : ""}`;
  return `/api/generations/${encodeURIComponent(id)}/audio?${q}`;
}

// Seconds of a file the server hands back when the browser should not decode the whole thing.
export const SERVER_DECODE_SECONDS = 600;

// Any audio ffmpeg reads (M4B included) as a mono WAV, first SERVER_DECODE_SECONDS only.
export async function decodeOnServer(file: File): Promise<ArrayBuffer> {
  const body = new FormData();
  body.append("file", file);
  const res = await check(await fetch(`/api/audio/decode?seconds=${SERVER_DECODE_SECONDS}`, { method: "POST", body }));
  return res.arrayBuffer();
}

// Re-encode a WAV that is not saved anywhere (a fresh generation) for download.
export async function convertWav(wav: Blob, format: string): Promise<Blob> {
  const body = new FormData();
  body.append("file", wav, "speech.wav");
  const res = await check(await fetch(`/api/convert?format=${encodeURIComponent(format)}`, { method: "POST", body }));
  return res.blob();
}

// Speak the fixed preview sentence; `cached` is true when the server reused an earlier render.
export async function previewVoice(id: string, model?: string | null): Promise<{ blob: Blob; cached: boolean; duration: number | null }> {
  const res = await check(
    await fetch(`/api/voices/${encodeURIComponent(id)}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: model || null }),
    })
  );
  return { blob: await res.blob(), cached: res.headers.get("X-Preview-Cached") === "true", duration: headerSeconds(res) };
}

export async function deleteVoice(id: string): Promise<void> {
  await check(await fetch(`/api/voices/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

export async function getGenerations(): Promise<GenerationList> {
  return json(await fetch("/api/generations"));
}

export interface SaveGenerationArgs {
  text: string;
  model?: string | null;
  voice?: string | null;
  language?: string | null;
  blob: Blob;
}

export async function saveGeneration({
  text,
  model,
  voice,
  language,
  blob,
}: SaveGenerationArgs): Promise<GenerationOut> {
  const body = new FormData();
  body.append("text", text);
  body.append("model", model || "");
  body.append("voice", voice || "");
  body.append("language", language || "en");
  body.append("file", blob, "speech.wav");
  return json(await fetch("/api/generations", { method: "POST", body }));
}

export async function deleteGeneration(id: string): Promise<void> {
  await check(await fetch(`/api/generations/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

export interface SynthesizeArgs {
  text: string;
  voice?: string | null;
  model?: string | null;
  language?: string;
  params?: Schemas["TTSRequest"]["params"];
  pauseMs?: number;
  save?: boolean;
}

// text may hold inline {Voice} tags; the server splits it per voice and stitches the clips.
// Returns the clip and, when save is on, its history id.
export async function synthesize({
  text,
  voice,
  model,
  language,
  params,
  pauseMs,
  save,
}: SynthesizeArgs): Promise<{ blob: Blob; id: string | null; duration: number | null }> {
  const res = await check(
    await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        voice: voice || null,
        model,
        language,
        params,
        pause_ms: pauseMs,
        save,
      }),
    })
  );
  return { blob: await res.blob(), id: res.headers.get("X-Generation-Id"), duration: headerSeconds(res) };
}
