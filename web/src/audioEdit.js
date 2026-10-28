// Pure helpers for the clip editor: no DOM, no Web Audio, so they run under `node --test`.

export const MIN_REGION_S = 0.05;
export const FADE_S = 0.008;

// Clamp to [0, duration], drop slivers, sort by start, merge overlaps.
export function normalizeRegions(regions, duration) {
  const clamped = regions
    .map((r) => ({
      start: Math.max(0, Math.min(r.start, r.end)),
      end: Math.min(duration, Math.max(r.start, r.end)),
    }))
    .filter((r) => r.end - r.start >= MIN_REGION_S)
    .sort((a, b) => a.start - b.start);
  const out = [];
  for (const r of clamped) {
    const last = out[out.length - 1];
    if (last && r.start <= last.end) last.end = Math.max(last.end, r.end);
    else out.push({ ...r });
  }
  return out;
}

export function totalDuration(regions) {
  return regions.reduce((sum, r) => sum + (r.end - r.start), 0);
}

// Concatenate the regions in time order, with a short linear fade at each seam.
export function joinRegions(samples, rate, regions, fadeS = FADE_S) {
  const parts = regions.map((r) => {
    const a = Math.max(0, Math.round(r.start * rate));
    const b = Math.min(samples.length, Math.round(r.end * rate));
    const part = samples.slice(a, Math.max(a, b));
    const fade = Math.min(Math.round(fadeS * rate), Math.floor(part.length / 2));
    for (let i = 0; i < fade; i++) {
      const g = i / fade;
      part[i] *= g;
      part[part.length - 1 - i] *= g;
    }
    return part;
  });
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0));
  let pos = 0;
  for (const p of parts) {
    out.set(p, pos);
    pos += p.length;
  }
  return out;
}

// Map a time inside the joined result back to a time in the source.
export function resultToSourceTime(regions, t) {
  let left = t;
  for (const r of regions) {
    const len = r.end - r.start;
    if (left <= len) return r.start + left;
    left -= len;
  }
  return regions.length ? regions[regions.length - 1].end : 0;
}

// Mono 16-bit PCM WAV.
export function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

// Min/max pairs per bucket for drawing.
export function peaks(samples, buckets) {
  const out = new Float32Array(buckets * 2);
  const step = samples.length / buckets;
  for (let b = 0; b < buckets; b++) {
    let lo = 0;
    let hi = 0;
    const from = Math.floor(b * step);
    const to = Math.min(samples.length, Math.max(from + 1, Math.floor((b + 1) * step)));
    for (let i = from; i < to; i++) {
      if (samples[i] < lo) lo = samples[i];
      if (samples[i] > hi) hi = samples[i];
    }
    out[b * 2] = lo;
    out[b * 2 + 1] = hi;
  }
  return out;
}

export function lengthWarning(seconds) {
  if (seconds <= 0) return "";
  if (seconds < 5) return "Under about 5 seconds is short for cloning.";
  if (seconds > 30) return "Over about 30 seconds adds little.";
  return "";
}
