import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";
import {
  joinRegions,
  lengthWarning,
  normalizeRegions,
  panView,
  peaks,
  resultToSourceTime,
  totalDuration,
  zoomView,
} from "./audioEdit.js";
import type { Region, View } from "./audioEdit.js";

const RATE = 24000;
const HEIGHT = 120;
const EDGE_PX = 6;
const WHEEL_ZOOM_PER_PX = 0.0015;
const BUTTON_ZOOM = 1.6;

// Waveform with keep-regions. Reports { samples, rate, regions } to onChange,
// or null when the file cannot be decoded.
export interface ClipEdit {
  samples: Float32Array<ArrayBuffer>;
  rate: number;
  regions: Region[];
}

interface Decoded {
  samples: Float32Array<ArrayBuffer>;
  rate: number;
  duration: number;
}

interface Playback {
  source: AudioBufferSourceNode;
  ctx: AudioContext;
  raf: number;
}

type Mode = "move" | "start" | "end";

interface Drag {
  mode: Mode;
  index: number;
  from: number;
  orig: Region;
}

interface Props {
  file: File;
  // Regions to start with once the file is decoded (re-trimming a saved voice).
  initialRegions?: Region[];
  onChange: (edit: ClipEdit | null) => void;
}

function cursorFor(mode: Mode | null, dragging: boolean): string {
  if (mode === "move") return dragging ? "grabbing" : "grab";
  if (mode) return "ew-resize";
  return "crosshair";
}

export default function ClipEditor({ file, initialRegions, onChange }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const audioRef = useRef<Decoded | null>(null);
  const playRef = useRef<Playback | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const [ready, setReady] = useState(false);
  const [regions, setRegions] = useState<Region[]>([]);
  const [selected, setSelected] = useState(-1);
  const [playhead, setPlayhead] = useState<number | null>(null);
  const [status, setStatus] = useState("");
  // null means the whole clip is visible.
  const [zoom, setZoom] = useState<View | null>(null);

  const stop = useCallback(() => {
    const p = playRef.current;
    if (!p) return;
    cancelAnimationFrame(p.raf);
    try {
      p.source.stop();
    } catch {}
    p.ctx.close();
    playRef.current = null;
    setPlayhead(null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    stop();
    audioRef.current = null;
    setReady(false);
    setRegions([]);
    setSelected(-1);
    setZoom(null);
    setStatus("Decoding...");
    onChange(null);
    (async () => {
      const ctx = new AudioContext({ sampleRate: RATE });
      try {
        const buf = await ctx.decodeAudioData(await file.arrayBuffer());
        if (cancelled) return;
        const ch = buf.getChannelData(0);
        const mono = new Float32Array(ch.length);
        for (let c = 0; c < buf.numberOfChannels; c++) {
          const d = buf.getChannelData(c);
          for (let i = 0; i < d.length; i++) mono[i] += d[i] / buf.numberOfChannels;
        }
        audioRef.current = { samples: mono, rate: buf.sampleRate, duration: buf.duration };
        setRegions(normalizeRegions(initialRegions ?? [], buf.duration));
        setReady(true);
        setStatus("");
      } catch {
        if (!cancelled) setStatus("This browser could not decode the file. It will be uploaded as is.");
      } finally {
        ctx.close();
      }
    })();
    return () => {
      cancelled = true;
      stop();
    };
  }, [file]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (ready && audioRef.current) onChange({ ...audioRef.current, regions });
  }, [regions, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  const viewOf = (a: Decoded): View => zoom ?? { start: 0, span: a.duration };

  // Draw.
  useEffect(() => {
    const canvas = canvasRef.current;
    const a = audioRef.current;
    if (!canvas || !a) return;
    const w = canvas.clientWidth;
    canvas.width = w;
    canvas.height = HEIGHT;
    const g = canvas.getContext("2d");
    if (!g) return;
    g.clearRect(0, 0, w, HEIGHT);
    const v = viewOf(a);
    const x = (t: number) => ((t - v.start) / v.span) * w;
    regions.forEach((r, i) => {
      g.fillStyle = i === selected ? "rgba(46,125,50,0.45)" : "rgba(46,125,50,0.25)";
      g.fillRect(x(r.start), 0, x(r.end) - x(r.start), HEIGHT);
      g.fillStyle = "#2e7d32";
      g.fillRect(x(r.start), 0, 2, HEIGHT);
      g.fillRect(x(r.end) - 2, 0, 2, HEIGHT);
    });
    const from = Math.max(0, Math.floor(v.start * a.rate));
    const to = Math.min(a.samples.length, Math.ceil((v.start + v.span) * a.rate));
    const p = peaks(a.samples.subarray(from, Math.max(from + 1, to)), w);
    g.fillStyle = "#888";
    const mid = HEIGHT / 2;
    for (let i = 0; i < w; i++) {
      const lo = p[i * 2] * mid;
      const hi = p[i * 2 + 1] * mid;
      g.fillRect(i, mid - hi, 1, Math.max(1, hi - lo));
    }
    if (playhead != null) {
      g.fillStyle = "#d32f2f";
      g.fillRect(x(playhead), 0, 2, HEIGHT);
    }
  }, [ready, regions, selected, playhead, zoom]);

  // React attaches wheel listeners as passive, which cannot stop the page from scrolling.
  // Zoom on the wheel, pan on Shift+wheel or a sideways swipe.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!ready || !canvas) return undefined;
    const onWheel = (e: WheelEvent) => {
      const a = audioRef.current;
      if (!a) return;
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const anchor = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const sideways = e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY);
      if (sideways) {
        const delta = e.shiftKey && e.deltaX === 0 ? e.deltaY : e.deltaX;
        setZoom((cur) => {
          const v = cur ?? { start: 0, span: a.duration };
          return panView(v, a.duration, (delta / rect.width) * v.span);
        });
      } else {
        const factor = Math.exp(-e.deltaY * WHEEL_ZOOM_PER_PX);
        setZoom((cur) => zoomView(cur ?? { start: 0, span: a.duration }, a.duration, anchor, factor));
      }
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [ready]);

  const timeAt = (e: PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    const a = audioRef.current;
    if (!canvas || !a) return 0;
    const v = viewOf(a);
    const rect = canvas.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    return v.start + f * v.span;
  };

  // Which region, and which part of it (an edge or its body), is under time t.
  const hitAt = (t: number, a: Decoded, canvas: HTMLCanvasElement): { index: number; mode: Mode } | null => {
    const slack = (EDGE_PX / canvas.getBoundingClientRect().width) * viewOf(a).span;
    const index = regions.findIndex((r) => t >= r.start - slack && t <= r.end + slack);
    if (index < 0) return null;
    const r = regions[index];
    if (Math.abs(t - r.start) <= slack) return { index, mode: "start" };
    if (Math.abs(t - r.end) <= slack) return { index, mode: "end" };
    return { index, mode: "move" };
  };

  function onPointerDown(e: PointerEvent<HTMLCanvasElement>) {
    const a = audioRef.current;
    const canvas = canvasRef.current;
    if (!ready || !a || !canvas) return;
    const t = timeAt(e);
    canvas.setPointerCapture(e.pointerId);
    const hit = hitAt(t, a, canvas);
    if (hit) {
      dragRef.current = { mode: hit.mode, index: hit.index, from: t, orig: { ...regions[hit.index] } };
      setSelected(hit.index);
      canvas.style.cursor = cursorFor(hit.mode, true);
    } else {
      const next = [...regions, { start: t, end: t }];
      dragRef.current = { mode: "end", index: next.length - 1, from: t, orig: { start: t, end: t } };
      setRegions(next);
      setSelected(next.length - 1);
      canvas.style.cursor = cursorFor("end", true);
    }
  }

  function onPointerMove(e: PointerEvent<HTMLCanvasElement>) {
    const d = dragRef.current;
    const a = audioRef.current;
    const canvas = canvasRef.current;
    if (!a || !canvas) return;
    if (!d) {
      // Hovering: show what a press here would do.
      canvas.style.cursor = cursorFor(hitAt(timeAt(e), a, canvas)?.mode ?? null, false);
      return;
    }
    const t = timeAt(e);
    setRegions((prev) => {
      const next = [...prev];
      const r = { ...d.orig };
      if (d.mode === "move") {
        const len = r.end - r.start;
        const start = Math.max(0, Math.min(a.duration - len, r.start + (t - d.from)));
        r.start = start;
        r.end = start + len;
      } else if (d.mode === "start") r.start = Math.min(t, r.end);
      else r.end = Math.max(t, r.start);
      next[d.index] = r;
      return next;
    });
  }

  function onPointerUp() {
    const d = dragRef.current;
    const a = audioRef.current;
    const canvas = canvasRef.current;
    if (!d || !a) return;
    dragRef.current = null;
    // Merge overlaps and drop slivers; keep the selection on the region holding the dragged one.
    const cur = regions[d.index];
    const next = normalizeRegions(regions, a.duration);
    setRegions(next);
    const mid = cur ? (cur.start + cur.end) / 2 : -1;
    setSelected(next.findIndex((r) => mid >= r.start && mid <= r.end));
    if (canvas) canvas.style.cursor = cursorFor(null, false);
  }

  function zoomBy(factor: number) {
    const a = audioRef.current;
    if (a) setZoom((cur) => zoomView(cur ?? { start: 0, span: a.duration }, a.duration, 0.5, factor));
  }

  function deleteSelected() {
    if (selected < 0) return;
    setRegions((prev) => prev.filter((_, i) => i !== selected));
    setSelected(-1);
  }

  function play(list: Region[] | null) {
    stop();
    const a = audioRef.current;
    if (!a) return;
    const samples = list ? joinRegions(a.samples, a.rate, list) : a.samples;
    if (!samples.length) return;
    const ctx = new AudioContext({ sampleRate: a.rate });
    const buf = ctx.createBuffer(1, samples.length, a.rate);
    buf.copyToChannel(samples, 0);
    const source = ctx.createBufferSource();
    source.buffer = buf;
    source.connect(ctx.destination);
    const startedAt = ctx.currentTime;
    const tick = () => {
      const elapsed = ctx.currentTime - startedAt;
      setPlayhead(list ? resultToSourceTime(list, elapsed) : elapsed);
      if (playRef.current) playRef.current.raf = requestAnimationFrame(tick);
    };
    source.onended = stop;
    playRef.current = { source, ctx, raf: 0 };
    source.start();
    tick();
  }

  const total = totalDuration(regions);
  const warn = lengthWarning(total);
  const duration = audioRef.current?.duration ?? 0;

  return (
    <div className="clip-editor">
      {status && <p className="hint">{status}</p>}
      {ready && (
        <>
          <canvas
            ref={canvasRef}
            className="clip-canvas"
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
          />
          {zoom && (
            <input
              type="range"
              className="clip-scroll"
              aria-label="Scroll the waveform"
              min={0}
              max={Math.max(0, duration - zoom.span)}
              step={0.01}
              value={zoom.start}
              onChange={(e) => setZoom({ start: Number(e.target.value), span: zoom.span })}
            />
          )}
          <div className="row">
            <button type="button" onClick={() => play(null)}>
              Play original
            </button>
            <button type="button" onClick={() => play(regions)} disabled={!regions.length}>
              Play result
            </button>
            <button type="button" onClick={stop}>
              Stop
            </button>
            <button type="button" onClick={deleteSelected} disabled={selected < 0}>
              Delete region
            </button>
            <button type="button" onClick={() => setRegions([])} disabled={!regions.length}>
              Clear regions
            </button>
            <button type="button" onClick={() => zoomBy(BUTTON_ZOOM)} aria-label="Zoom in">
              +
            </button>
            <button type="button" onClick={() => zoomBy(1 / BUTTON_ZOOM)} disabled={!zoom} aria-label="Zoom out">
              -
            </button>
            <button type="button" onClick={() => setZoom(null)} disabled={!zoom}>
              Reset zoom
            </button>
          </div>
          <p className="hint">
            Source {duration.toFixed(1)}s.{" "}
            {regions.length
              ? `Keeping ${regions.length} region${regions.length > 1 ? "s" : ""}, ${total.toFixed(1)}s total.`
              : "Drag on the waveform to mark the parts to keep. With no regions, the whole file is saved."}{" "}
            {warn} Mouse wheel zooms, Shift+wheel scrolls. Drag a region's edge to resize it, or its body to move it.
          </p>
        </>
      )}
    </div>
  );
}
