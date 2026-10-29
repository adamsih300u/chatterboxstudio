import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  addVoice,
  deleteVoice,
  errorMessage,
  getVoiceSource,
  previewVoice,
  setBuiltinHidden,
  setDefaultVoice,
  updateVoice,
  voiceExportUrl,
} from "./api";
import type { VoiceOut } from "./api";
import ClipEditor from "./ClipEditor";
import { FormatSelect, useExportFormat } from "./ExportFormat";
import Player from "./Player";
import MoreMenu from "./MoreMenu";
import type { ClipEdit } from "./ClipEditor";
import { encodeWav, joinRegions, needsServerDecode } from "./audioEdit.js";

interface Props {
  model: string;
  voices: VoiceOut[];
  defaultVoice: string;
  builtinHidden: boolean;
  refreshVoices: () => Promise<void>;
  setError: (message: string) => void;
}

interface Preview {
  busy: boolean;
  url?: string;
  cached?: boolean;
  duration?: number | null;
}

interface Editing {
  voice: VoiceOut;
  source: File | null; // null while the source loads
}

// The kept parts joined into a wav; with no regions, the whole decoded clip.
function clipFrom(edit: ClipEdit): File {
  const regions = edit.regions.length ? edit.regions : [{ start: 0, end: edit.samples.length / edit.rate }];
  const wav = encodeWav(joinRegions(edit.samples, edit.rate, regions), edit.rate);
  return new File([wav], "clip.wav", { type: "audio/wav" });
}

export default function VoicesTab({ model, voices, defaultVoice, builtinHidden, refreshVoices, setError }: Props) {
  const [name, setName] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [edit, setEdit] = useState<ClipEdit | null>(null);
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const previewUrls = useRef<Set<string>>(new Set());
  const [editing, setEditing] = useState<Editing | null>(null);
  const [reEdit, setReEdit] = useState<ClipEdit | null>(null);
  const [saving, setSaving] = useState(false);
  const [format, setFormat] = useExportFormat();

  // A stable array: ClipEditor re-decodes whenever `files` changes identity.
  const editSource = editing?.source ?? null;
  const editFiles = useMemo(() => (editSource ? [editSource] : []), [editSource]);

  useEffect(() => {
    const urls = previewUrls.current;
    return () => urls.forEach((u) => URL.revokeObjectURL(u));
  }, []);

  // A single ordinary upload is sent untouched. Several files, or one the server had to decode
  // (an audiobook), are sent as the decoded audio the editor holds.
  const rawSingle = files.length === 1 && !needsServerDecode(files[0].name, files[0].size);

  async function onSave(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!files.length || !name.trim()) return;
    setError("");
    try {
      if (!rawSingle) {
        // The decoded audio becomes the original, so the voice can be re-trimmed later.
        if (!edit) return;
        const combined = new File([encodeWav(edit.samples, edit.rate)], "sources.wav", { type: "audio/wav" });
        await addVoice(name.trim(), clipFrom(edit), combined, edit.regions);
      } else if (edit && edit.regions.length) {
        // Keep the untrimmed upload and the cut so the voice can be re-trimmed later.
        await addVoice(name.trim(), clipFrom(edit), files[0], edit.regions);
      } else {
        await addVoice(name.trim(), files[0]);
      }
      setName("");
      setFiles([]);
      setEdit(null);
      if (fileRef.current) fileRef.current.value = "";
      await refreshVoices();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function onDefault(id: string | null) {
    setError("");
    try {
      await setDefaultVoice(id);
      await refreshVoices();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function onToggleBuiltin() {
    setError("");
    try {
      await setBuiltinHidden(!builtinHidden);
      await refreshVoices();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function onDelete(id: string) {
    setError("");
    try {
      await deleteVoice(id);
      dropPreview(id);
      if (editing?.voice.id === id) setEditing(null);
      await refreshVoices();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  function dropPreview(id: string) {
    setPreviews((prev) => {
      const { [id]: gone, ...rest } = prev;
      if (gone?.url) {
        URL.revokeObjectURL(gone.url);
        previewUrls.current.delete(gone.url);
      }
      return rest;
    });
  }

  async function onPreview(id: string) {
    setError("");
    setPreviews((prev) => ({ ...prev, [id]: { ...prev[id], busy: true } }));
    try {
      const { blob, cached, duration } = await previewVoice(id, model);
      const url = URL.createObjectURL(blob);
      previewUrls.current.add(url);
      setPreviews((prev) => {
        const old = prev[id]?.url;
        if (old) {
          URL.revokeObjectURL(old);
          previewUrls.current.delete(old);
        }
        return { ...prev, [id]: { busy: false, url, cached, duration } };
      });
    } catch (err) {
      setPreviews((prev) => ({ ...prev, [id]: { ...prev[id], busy: false } }));
      setError(errorMessage(err));
    }
  }

  async function onEdit(voice: VoiceOut) {
    setError("");
    setReEdit(null);
    setEditing({ voice, source: null });
    try {
      const blob = await getVoiceSource(voice.id);
      setEditing({ voice, source: new File([blob], voice.id) });
    } catch (err) {
      setEditing(null);
      setError(errorMessage(err));
    }
  }

  async function onSaveEdit() {
    if (!editing || !reEdit) return;
    setError("");
    setSaving(true);
    try {
      await updateVoice(editing.voice.id, clipFrom(reEdit), reEdit.regions);
      dropPreview(editing.voice.id);
      setEditing(null);
      setReEdit(null);
      await refreshVoices();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <form onSubmit={onSave} className="row">
        <input placeholder="Voice name" value={name} onChange={(e) => setName(e.target.value)} />
        <input
          type="file"
          multiple
          accept=".wav,.mp3,.flac,.ogg,.m4a,.m4b,.aac,.opus,audio/*"
          ref={fileRef}
          onChange={(e) => {
            const picked = Array.from(e.target.files ?? []);
            // Picking again adds to the list, so clips can be gathered one batch at a time.
            if (picked.length) setFiles((cur) => [...cur, ...picked]);
            e.target.value = "";
          }}
        />
        <button type="submit" disabled={!name.trim() || !files.length || (!rawSingle && !edit)}>
          Save voice
        </button>
      </form>
      {files.length > 1 && (
        <ol className="hint file-list">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`}>
              {f.name}{" "}
              <button type="button" className="link" onClick={() => setFiles((cur) => cur.filter((_, j) => j !== i))}>
                Remove
              </button>
            </li>
          ))}
        </ol>
      )}
      {files.length === 1 && (
        <p className="hint">
          {files[0].name}{" "}
          <button type="button" className="link" onClick={() => setFiles([])}>
            Remove
          </button>
        </p>
      )}
      {files.length > 0 && <ClipEditor files={files} onChange={setEdit} />}
      <p className="hint">Upload a clean 5 to 15 second reference clip of a single speaker, or several files to pick the best parts of each into one clip. Saved voices appear in the Generate tab. The default voice is preselected there.</p>

      {editing && (
        <div className="edit-card">
          <strong>Re-trimming {editing.voice.name}</strong>
          {!editing.source && <p className="hint">Loading the original...</p>}
          {editing.source && (
            <ClipEditor files={editFiles} initialRegions={editing.voice.regions} onChange={setReEdit} />
          )}
          {editing.source && !editing.voice.has_original && (
            <p className="hint">This voice was saved before originals were kept, so you are editing its current clip. Saving keeps that clip as the original from now on.</p>
          )}
          <div className="row">
            <button type="button" onClick={onSaveEdit} disabled={!reEdit || saving}>
              {saving ? "Saving..." : "Save changes"}
            </button>
            <button type="button" onClick={() => setEditing(null)} disabled={saving}>
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="voices-toolbar">
        <span className="hint">Export format</span>
        <FormatSelect value={format} onChange={setFormat} label="Export format" />
      </div>
      <ul className="voices">
        <li>
          <span>
            Built-in voice <span className="hint">(the selected model's own voice)</span>
            {!defaultVoice && <strong className="badge"> (default)</strong>}
            {builtinHidden && <span className="hint"> hidden</span>}
          </span>
          <button onClick={() => onDefault(null)} disabled={!defaultVoice}>
            Set default
          </button>
          <button onClick={onToggleBuiltin}>{builtinHidden ? "Show" : "Hide"}</button>
        </li>
        {voices.length === 0 && <li className="hint">No saved voices yet.</li>}
        {voices.map((v) => {
          const preview = previews[v.id];
          return (
            <li key={v.id}>
              <span>
                {v.name}
                {v.id === defaultVoice && <strong className="badge"> (default)</strong>}
              </span>
              <div className="voice-player">
                {preview?.url ? (
                  <>
                    <Player src={preview.url} duration={preview.duration} autoPlay />
                    <span className="hint">Preview, {preview.cached ? "from cache" : "freshly rendered"}</span>
                    <button type="button" className="link" title="Back to the saved clip" onClick={() => dropPreview(v.id)}>
                      ✕
                    </button>
                  </>
                ) : (
                  <Player src={`/api/voices/${encodeURIComponent(v.id)}/audio?v=${v.updated}`} duration={v.duration} />
                )}
              </div>
              <button onClick={() => onPreview(v.id)} disabled={preview?.busy} title="Speak a sample sentence in this voice with the selected model">
                {preview?.busy ? "Rendering..." : "Preview"}
              </button>
              <MoreMenu>
                <button type="button" role="menuitem" onClick={() => onEdit(v)} disabled={editing?.voice.id === v.id}>
                  Edit
                </button>
                <a role="menuitem" href={voiceExportUrl(v.id, format)} download>
                  Export as {format.toUpperCase()}
                </a>
                <button type="button" role="menuitem" onClick={() => onDefault(v.id === defaultVoice ? null : v.id)}>
                  {v.id === defaultVoice ? "Clear default" : "Set default"}
                </button>
                <button type="button" role="menuitem" className="menu-danger" onClick={() => onDelete(v.id)}>
                  Delete
                </button>
              </MoreMenu>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
