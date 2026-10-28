async function check(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* body was not JSON */
    }
    throw new Error(detail);
  }
  return res;
}

export async function getModels() {
  return (await check(await fetch("/api/models"))).json();
}

export async function loadModel(id) {
  return (await check(await fetch(`/api/models/${encodeURIComponent(id)}/load`, { method: "POST" }))).json();
}

export async function getVoices() {
  return (await check(await fetch("/api/voices"))).json();
}

export async function setDefaultVoice(id) {
  const res = await fetch("/api/voices/default", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: id || null }),
  });
  return (await check(res)).json();
}

export async function setBuiltinHidden(hidden) {
  const res = await fetch("/api/voices/builtin", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hidden }),
  });
  return (await check(res)).json();
}

export async function addVoice(name, file) {
  const body = new FormData();
  body.append("name", name);
  body.append("file", file);
  return (await check(await fetch("/api/voices", { method: "POST", body }))).json();
}

export async function deleteVoice(id) {
  await check(await fetch(`/api/voices/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

export async function getGenerations() {
  return (await check(await fetch("/api/generations"))).json();
}

export async function saveGeneration({ text, model, voice, language, blob }) {
  const body = new FormData();
  body.append("text", text);
  body.append("model", model || "");
  body.append("voice", voice || "");
  body.append("language", language || "en");
  body.append("file", blob, "speech.wav");
  return (await check(await fetch("/api/generations", { method: "POST", body }))).json();
}

export async function deleteGeneration(id) {
  await check(await fetch(`/api/generations/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

// text may hold inline {Voice} tags; the server splits it per voice and stitches the clips.
// Returns the clip and, when save is on, its history id.
export async function synthesize({ text, voice, model, language, params, pauseMs, save }) {
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
