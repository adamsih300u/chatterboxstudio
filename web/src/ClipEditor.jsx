import { useCallback, useEffect, useRef, useState } from "react";
import {
  joinRegions,
  lengthWarning,
  normalizeRegions,
  peaks,
  resultToSourceTime,
  totalDuration,
} from "./audioEdit.js";

const RATE = 24000;
const HEIGHT = 120;
const EDGE_PX = 6;

// Waveform with keep-regions. Reports { samples, rate, regions } to onChange,
// or null when the file cannot be decoded.
export default function ClipEditor({ file, onChange }) {
  const canvasRef = useRef(null);
  const audioRef = useRef(null); // { samples, rate, duration }
  const playRef = useRef(null); // { source, ctx, raf }
  const dragRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [regions, setRegions] = useState([]);
  const [selected, setSelected] = useState(-1);
  const [playhead, setPlayhead] = useState(null);
  const [status, setStatus] = useState("");

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
    if (ready) onChange({ ...audioRef.current, regions });
  }, [regions, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  // Draw.
  useEffect(() => {
    const canvas = canvasRef.current;
    const a = audioRef.current;
    if (!canvas || !a) return;
    const w = canvas.clientWidth;
    canvas.width = w;
    canvas.height = HEIGHT;
    const g = canvas.getContext("2d");
    g.clearRect(0, 0, w, HEIGHT);
    const x = (t) => (t / a.duration) * w;
    regions.forEach((r, i) => {
      g.fillStyle = i === selected ? "rgba(46,125,50,0.45)" : "rgba(46,125,50,0.25)";
      g.fillRect(x(r.start), 0, x(r.end) - x(r.start), HEIGHT);
      g.fillStyle = "#2e7d32";
      g.fillRect(x(r.start), 0, 2, HEIGHT);
      g.fillRect(x(r.end) - 2, 0, 2, HEIGHT);
    });
    const p = peaks(a.samples, w);
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
  }, [ready, regions, selected, playhead]);

  const timeAt = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    return f * audioRef.current.duration;
  };

  function onPointerDown(e) {
    if (!ready) return;
    const a = audioRef.current;
    const rect = canvasRef.current.getBoundingClientRect();
    const t = timeAt(e);
    const slack = (EDGE_PX / rect.width) * a.duration;
    canvasRef.current.setPointerCapture(e.pointerId);
    const hit = regions.findIndex((r) => t >= r.start - slack && t <= r.end + slack);
    if (hit >= 0) {
      const r = regions[hit];
      let mode = "move";
      if (Math.abs(t - r.start) <= slack) mode = "start";
      else if (Math.abs(t - r.end) <= slack) mode = "end";
      dragRef.current = { mode, index: hit, from: t, orig: { ...r } };
      setSelected(hit);
    } else {
      const next = [...regions, { start: t, end: t }];
      dragRef.current = { mode: "end", index: next.length - 1, from: t, orig: { start: t, end: t } };
      setRegions(next);
      setSelected(next.length - 1);
    }
  }

  function onPointerMove(e) {
    const d = dragRef.current;
    if (!d) return;
    const a = audioRef.current;
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
    if (!d) return;
    dragRef.current = null;
    // Merge overlaps and drop slivers; keep the selection on the region holding the dragged one.
    const cur = regions[d.index];
    const next = normalizeRegions(regions, audioRef.current.duration);
    setRegions(next);
    const mid = cur ? (cur.start + cur.end) / 2 : -1;
    setSelected(next.findIndex((r) => mid >= r.start && mid <= r.end));
  }

  function deleteSelected() {
    if (selected < 0) return;
    setRegions((prev) => prev.filter((_, i) => i !== selected));
    setSelected(-1);
  }

  function play(list) {
    stop();
    const a = audioRef.current;
    const isResult = list !== null;
    const samples = isResult ? joinRegions(a.samples, a.rate, list) : a.samples;
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
      setPlayhead(isResult ? resultToSourceTime(list, elapsed) : elapsed);
      if (playRef.current) playRef.current.raf = requestAnimationFrame(tick);
    };
    source.onended = stop;
    playRef.current = { source, ctx, raf: 0 };
    source.start();
    tick();
  }

  const total = totalDuration(regions);
  const warn = lengthWarning(total);

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
          </div>
          <p className="hint">
            Source {audioRef.current.duration.toFixed(1)}s.{" "}
            {regions.length
              ? `Keeping ${regions.length} region${regions.length > 1 ? "s" : ""}, ${total.toFixed(1)}s total.`
              : "Drag on the waveform to mark the parts to keep. With no regions, the whole file is saved."}{" "}
            {warn}
          </p>
        </>
      )}
    </div>
  );
}
