import { useEffect, useMemo, useRef, useState } from "react";
import { addSource, deleteMood, deleteSource, errorMessage, getSourceAudio, moodAudioUrl, previewVoice, putMood } from "./api";
import type { VoiceOut } from "./api";
import ClipEditor from "./ClipEditor";
import type { ClipEdit } from "./ClipEditor";
import Player from "./Player";
import { encodeWav, joinRegions } from "./audioEdit.js";

// The kept parts joined into a wav; with no regions, the whole decoded clip.
export function clipFrom(edit: ClipEdit): File {
  const regions = edit.regions.length ? edit.regions : [{ start: 0, end: edit.samples.length / edit.rate }];
  const wav = encodeWav(joinRegions(edit.samples, edit.rate, regions), edit.rate);
  return new File([wav], "clip.wav", { type: "audio/wav" });
}

interface Cutting {
  mood: string; // name typed for a new mood, or an existing mood's name
  isNew: boolean;
  sourceId: string;
  file: File | null; // null while the upload loads
  initial: { start: number; end: number }[];
}

interface Props {
  voice: VoiceOut;
  model: string;
  refreshVoices: () => Promise<void>;
  setError: (message: string) => void;
}

// Moods of one voice (clips for neutral, angry, whisper...) and the uploads they are cut from.
export default function MoodPanel({ voice, model, refreshVoices, setError }: Props) {
  const [newName, setNewName] = useState("");
  const [pickSource, setPickSource] = useState(voice.sources[0]?.id ?? "");
  const [cutting, setCutting] = useState<Cutting | null>(null);
  const [cut, setCut] = useState<ClipEdit | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<{ mood: string; url: string } | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);

  const cutFiles = useMemo(() => (cutting?.file ? [cutting.file] : []), [cutting?.file]);

  useEffect(() => {
    if (!voice.sources.some((s) => s.id === pickSource)) setPickSource(voice.sources[0]?.id ?? "");
  }, [voice.sources, pickSource]);

  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl.url); }, [previewUrl]);

  async function run(work: () => Promise<void>) {
    setError("");
    setBusy(true);
    try {
      await work();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function startCut(mood: string, isNew: boolean, sourceId: string, initial: { start: number; end: number }[]) {
    setCut(null);
    setCutting({ mood, isNew, sourceId, file: null, initial });
    try {
      const blob = await getSourceAudio(voice.id, sourceId);
      setCutting({ mood, isNew, sourceId, file: new File([blob], `${voice.id}-${sourceId}`), initial });
    } catch (err) {
      setCutting(null);
      setError(errorMessage(err));
    }
  }

  function editMood(moodId: string) {
    const m = voice.moods.find((x) => x.id === moodId);
    if (!m) return;
    const sourceId = m.regions[0]?.source || voice.sources[0]?.id;
    if (!sourceId) return;
    const initial = m.regions.filter((r) => (r.source || sourceId) === sourceId).map(({ start, end }) => ({ start, end }));
    void startCut(m.name, false, sourceId, initial);
  }

  async function saveCut() {
    if (!cutting || !cut) return;
    await run(async () => {
      const regions = cut.regions.map((r) => ({ ...r, source: cutting.sourceId }));
      await putMood(voice.id, cutting.mood, clipFrom(cut), regions);
      setCutting(null);
      setCut(null);
      setNewName("");
      await refreshVoices();
    });
  }

  async function preview(moodId: string) {
    await run(async () => {
      const { blob } = await previewVoice(voice.id, model, moodId);
      setPreviewUrl({ mood: moodId, url: URL.createObjectURL(blob) });
    });
  }

  const canCut = voice.sources.length > 0;

  return (
    <div className="mood-panel" style={{ flexBasis: "100%" }}>
      <ul className="voices">
        {voice.moods.map((m) => (
          <li key={m.id}>
            <span>
              {m.name} <span className="hint">{m.id === "neutral" ? "{" + voice.name + "}" : `{${m.name}} or {${voice.name}:${m.name}}`}</span>
            </span>
            <div className="voice-player">
              {previewUrl?.mood === m.id ? (
                <>
                  <Player src={previewUrl.url} duration={null} autoPlay />
                  <button type="button" className="link" onClick={() => setPreviewUrl(null)}>✕</button>
                </>
              ) : (
                <Player src={moodAudioUrl(voice.id, m.id, m.updated)} duration={m.duration} />
              )}
            </div>
            <button type="button" onClick={() => preview(m.id)} disabled={busy} title="Speak a sample sentence with this mood">
              Preview
            </button>
            {canCut && (
              <button type="button" onClick={() => editMood(m.id)} disabled={busy || !!cutting}>
                Re-cut
              </button>
            )}
            {m.id !== "neutral" && (
              <button type="button" disabled={busy} onClick={() => run(async () => { await deleteMood(voice.id, m.id); await refreshVoices(); })}>
                Delete
              </button>
            )}
          </li>
        ))}
      </ul>

      <div className="row">
        <input placeholder="New mood (angry, whisper...)" value={newName} onChange={(e) => setNewName(e.target.value)} disabled={!!cutting} />
        <select value={pickSource} onChange={(e) => setPickSource(e.target.value)} disabled={!canCut || !!cutting} aria-label="Cut from upload">
          {voice.sources.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
        <button type="button" disabled={!newName.trim() || !pickSource || !!cutting || busy} onClick={() => startCut(newName.trim(), true, pickSource, [])}>
          Cut mood
        </button>
      </div>
      {!canCut && <p className="hint">This voice has no kept upload yet. Add one below, then cut moods from it.</p>}

      {cutting && (
        <div className="edit-card">
          <strong>{cutting.isNew ? `New mood: ${cutting.mood}` : `Re-cutting ${cutting.mood}`}</strong>
          {!cutting.file && <p className="hint">Loading the upload...</p>}
          {cutting.file && <ClipEditor files={cutFiles} initialRegions={cutting.initial} onChange={setCut} />}
          <p className="hint">Put the clearest, most typical moment of this mood first: only the first 10 seconds of a clip steer the voice on the pinned model.</p>
          <div className="row">
            <button type="button" onClick={saveCut} disabled={!cut || busy}>{busy ? "Saving..." : "Save mood"}</button>
            <button type="button" onClick={() => { setCutting(null); setCut(null); }} disabled={busy}>Cancel</button>
          </div>
        </div>
      )}

      <div className="row">
        <span className="hint">Uploads:</span>
        {voice.sources.map((s) => (
          <span key={s.id} className="hint">
            {s.name}{" "}
            <button type="button" className="link" disabled={busy} onClick={() => run(async () => { await deleteSource(voice.id, s.id); await refreshVoices(); })}>
              Remove
            </button>
          </span>
        ))}
        <input
          type="file"
          ref={uploadRef}
          accept=".wav,.mp3,.flac,.ogg,.m4a,.m4b,.aac,.opus,audio/*"
          disabled={busy}
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) void run(async () => { await addSource(voice.id, f); await refreshVoices(); });
          }}
          title="Add another upload to cut moods from"
        />
      </div>
    </div>
  );
}
