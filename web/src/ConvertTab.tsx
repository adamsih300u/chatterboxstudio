import { useEffect, useRef, useState } from "react";
import { convertVoice, errorMessage } from "./api";
import type { VoiceOut } from "./api";
import Player from "./Player";

interface Props {
  voices: VoiceOut[];
  defaultVoice: string;
  builtinHidden: boolean;
  setError: (message: string) => void;
}

interface Result {
  url: string;
  duration: number | null;
  seed: number | null;
}

// Perform a line yourself (record it or upload it), then hear it in a saved voice. Pacing, timing and
// emphasis come from the recording; only the voice changes.
export default function ConvertTab({ voices, defaultVoice, builtinHidden, setError }: Props) {
  const [recording, setRecording] = useState<File | null>(null);
  const [recordingUrl, setRecordingUrl] = useState("");
  const [voice, setVoice] = useState(defaultVoice || (builtinHidden ? (voices[0]?.id ?? "") : ""));
  const [mood, setMood] = useState("");
  const [recorder, setRecorder] = useState<MediaRecorder | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const resultUrl = useRef("");
  const recorderRef = useRef<MediaRecorder | null>(null);

  const picked = voices.find((v) => v.id === voice);
  const canRecord = typeof MediaRecorder !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

  // The mood list belongs to the chosen voice.
  useEffect(() => {
    if (mood && !picked?.moods.some((m) => m.id === mood)) setMood("");
  }, [picked, mood]);

  useEffect(() => {
    if (!recording) return undefined;
    const url = URL.createObjectURL(recording);
    setRecordingUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [recording]);

  useEffect(
    () => () => {
      if (resultUrl.current) URL.revokeObjectURL(resultUrl.current);
      // Leaving the tab mid-recording must release the microphone.
      const rec = recorderRef.current;
      if (rec && rec.state !== "inactive") {
        rec.ondataavailable = null;
        rec.onstop = null;
        rec.stream.getTracks().forEach((t) => t.stop());
        rec.stop();
      }
    },
    []
  );

  function setRecordingFile(file: File | null) {
    setRecording(file);
    if (!file) setRecordingUrl("");
    clearResult();
  }

  function clearResult() {
    if (resultUrl.current) URL.revokeObjectURL(resultUrl.current);
    resultUrl.current = "";
    setResult(null);
  }

  async function startRecording() {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecorder(null);
        if (chunks.length) setRecordingFile(new File(chunks, "recording.webm", { type: rec.mimeType || "audio/webm" }));
      };
      rec.start();
      recorderRef.current = rec;
      setRecorder(rec);
    } catch (err) {
      setError(`Could not use the microphone: ${errorMessage(err)}`);
    }
  }

  async function convert() {
    if (!recording) return;
    setError("");
    setBusy(true);
    try {
      const { blob, duration, seed } = await convertVoice(recording, voice, mood);
      clearResult();
      resultUrl.current = URL.createObjectURL(blob);
      setResult({ url: resultUrl.current, duration, seed });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <p className="hint">
        Record or upload a line, then hear it in one of your voices. Timing, pacing and emphasis stay yours; only the voice changes.
        The first conversion loads the model and takes a while.
      </p>

      <div className="row">
        {canRecord &&
          (recorder ? (
            <button type="button" onClick={() => recorder.stop()}>
              Stop recording
            </button>
          ) : (
            <button type="button" onClick={startRecording} disabled={busy}>
              Record
            </button>
          ))}
        <input
          type="file"
          accept=".wav,.mp3,.flac,.ogg,.m4a,.aac,.opus,.webm,audio/*"
          disabled={busy || !!recorder}
          aria-label="Recording to convert"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) setRecordingFile(f);
          }}
        />
        {recorder && <span className="hint">Recording...</span>}
      </div>

      {recording && recordingUrl && (
        <div className="row">
          <span className="hint">{recording.name}</span>
          <Player src={recordingUrl} duration={null} />
          <button type="button" className="link" onClick={() => setRecordingFile(null)} disabled={busy}>
            Remove
          </button>
        </div>
      )}

      <div className="row">
        <label>
          Voice{" "}
          <select value={voice} onChange={(e) => { setVoice(e.target.value); clearResult(); }} disabled={busy}>
            {!builtinHidden && <option value="">Built-in voice</option>}
            {voices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        {picked && picked.moods.length > 1 && (
          <label>
            Mood{" "}
            <select value={mood} onChange={(e) => { setMood(e.target.value); clearResult(); }} disabled={busy}>
              <option value="">Neutral</option>
              {picked.moods
                .filter((m) => m.id !== "neutral")
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
            </select>
          </label>
        )}
        <button type="button" onClick={convert} disabled={!recording || busy || !!recorder}>
          {busy ? "Converting..." : "Convert"}
        </button>
      </div>

      {result && (
        <div className="row">
          <Player src={result.url} duration={result.duration} autoPlay />
          <a href={result.url} download="converted.wav">
            Download
          </a>
          {result.seed != null && <span className="hint">seed {result.seed}</span>}
        </div>
      )}
    </section>
  );
}
