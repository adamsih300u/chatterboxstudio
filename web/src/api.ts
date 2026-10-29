import type { components } from "./api-types";

type Schemas = components["schemas"];

export type ModelInfo = Schemas["ModelInfo"];
export type ModelList = Schemas["ModelList"];
export type LoadStarted = Schemas["LoadStarted"];
export type VoiceOut = Schemas["VoiceOut"];
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

export async function addVoice(name: string, file: Blob): Promise<VoiceOut> {
  const body = new FormData();
  body.append("name", name);
  body.append("file", file);
  return json(await fetch("/api/voices", { method: "POST", body }));
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
}: SynthesizeArgs): Promise<{ blob: Blob; id: string | null }> {
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
  return { blob: await res.blob(), id: res.headers.get("X-Generation-Id") };
}
