import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { addVoice, deleteVoice, errorMessage, setBuiltinHidden, setDefaultVoice } from "./api";
import type { VoiceOut } from "./api";
import ClipEditor from "./ClipEditor";
import type { ClipEdit } from "./ClipEditor";
import { encodeWav, joinRegions } from "./audioEdit.js";

interface Props {
  voices: VoiceOut[];
  defaultVoice: string;
  builtinHidden: boolean;
  refreshVoices: () => Promise<void>;
  setError: (message: string) => void;
}

export default function VoicesTab({ voices, defaultVoice, builtinHidden, refreshVoices, setError }: Props) {
  const [name, setName] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [edit, setEdit] = useState<ClipEdit | null>(null);

  async function onSave(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!file || !name.trim()) return;
    setError("");
    try {
      let upload: Blob = file;
      if (edit && edit.regions.length) {
        const wav = encodeWav(joinRegions(edit.samples, edit.rate, edit.regions), edit.rate);
        upload = new File([wav], "clip.wav", { type: "audio/wav" });
      }
      await addVoice(name.trim(), upload);
      setName("");
      setFile(null);
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
      await refreshVoices();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <section>
      <form onSubmit={onSave} className="row">
        <input placeholder="Voice name" value={name} onChange={(e) => setName(e.target.value)} />
        <input type="file" accept=".wav,.mp3,.flac,.ogg,.m4a,audio/*" ref={fileRef} onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <button type="submit" disabled={!name.trim() || !file}>
          Save voice
        </button>
      </form>
      {file && <ClipEditor file={file} onChange={setEdit} />}
      <p className="hint">Upload a clean 5 to 15 second reference clip of a single speaker. Saved voices appear in the Generate tab. The default voice is preselected there.</p>
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
        {voices.map((v) => (
          <li key={v.id}>
            <span>
              {v.name}
              {v.id === defaultVoice && <strong className="badge"> (default)</strong>}
            </span>
            <audio controls preload="none" src={`/api/voices/${encodeURIComponent(v.id)}/audio`} />
            <button onClick={() => onDefault(v.id === defaultVoice ? null : v.id)}>
              {v.id === defaultVoice ? "Clear default" : "Set default"}
            </button>
            <button className="danger" onClick={() => onDelete(v.id)}>
              Delete
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
