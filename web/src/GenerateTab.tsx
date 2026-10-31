import { useEffect, useRef, useState } from "react";
import AdvancedParams from "./AdvancedParams";
import type { ParamValue, ParamValues } from "./AdvancedParams";
import TagTextarea, { TURBO_TAGS } from "./TagTextarea";
import { canStream, convertWav, errorMessage, synthesize, synthesizeStreaming } from "./api";
import { FormatSelect, saveBlob, useExportFormat } from "./ExportFormat";
import Player from "./Player";
import type { ModelInfo, VoiceOut } from "./api";

const STATE_LABEL: Record<ModelInfo["state"], string> = { idle: "not loaded", loading: "loading...", ready: "", error: "failed to load" };

const LANGUAGES = [
  ["en", "English"], ["ar", "Arabic"], ["da", "Danish"], ["de", "German"], ["el", "Greek"],
  ["es", "Spanish"], ["fi", "Finnish"], ["fr", "French"], ["he", "Hebrew"], ["hi", "Hindi"],
  ["it", "Italian"], ["ja", "Japanese"], ["ko", "Korean"], ["ms", "Malay"], ["nl", "Dutch"],
  ["no", "Norwegian"], ["pl", "Polish"], ["pt", "Portuguese"], ["ru", "Russian"], ["sv", "Swedish"],
  ["sw", "Swahili"], ["tr", "Turkish"], ["zh", "Chinese"],
];

const VOICE_TAG = /\{[^{}\n]{1,64}\}/;

// Settings handed over from History's "Reuse"; nonce changes on every click so the same entry can be reused again.
export interface Reuse {
  nonce: number;
  text?: string;
  model: string;
  voice?: string;
  params: ParamValues;
  seed?: number | null;
}

interface Props {
  reuse?: Reuse | null;
  models: ModelInfo[];
  model: string;
  setModel: (id: string) => void;
  voices: VoiceOut[];
  defaultVoice: string;
  builtinHidden: boolean;
  onLoadModel: () => void;
  refreshModels: () => Promise<void>;
  refreshGenerations: () => Promise<void>;
  setError: (message: string) => void;
}

export default function GenerateTab({ reuse, models, model, setModel, voices, defaultVoice, builtinHidden, onLoadModel, refreshModels, refreshGenerations, setError }: Props) {
  const [text, setText] = useState("");
  const [voice, setVoice] = useState("");
  const [language, setLanguage] = useState("en");
  // Overrides per variant, so switching models does not leak settings across.
  const [overrides, setOverrides] = useState<Record<string, ParamValues>>({});
  const [pauseMs, setPauseMs] = useState(300);
  const [keep, setKeep] = useState(true);
  const [stream, setStream] = useState(true);
  const [hearing, setHearing] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [audioLength, setAudioLength] = useState<number | null>(null);
  const [format, setFormat] = useExportFormat();
  const [converting, setConverting] = useState(false);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  // Empty means a random take each time; a number repeats that take for the same text and settings.
  const [seedText, setSeedText] = useState("");
  const [lastSeed, setLastSeed] = useState<number | null>(null);

  useEffect(() => {
    if (!reuse) return;
    if (reuse.text !== undefined) setText(reuse.text);
    if (reuse.voice !== undefined) setVoice(reuse.voice);
    setOverrides((o) => ({ ...o, [reuse.model]: reuse.params || {} }));
    setSeedText(reuse.seed != null ? String(reuse.seed) : "");
  }, [reuse?.nonce]);

  // Follow the saved default whenever it changes (it loads after first render).
  useEffect(() => {
    setVoice(defaultVoice || "");
  }, [defaultVoice]);

  // The model always has its built-in voice, so keep the option when nothing else could be picked.
  const showBuiltin = !builtinHidden || voices.length === 0 || voice === "";
  const selected = models.find((m) => m.id === model);
  const ready = selected?.state === "ready";
  const multi = VOICE_TAG.test(text);
  const values = overrides[model] || {};
  const canGenerate = ready && !busy && text.trim() !== "";

  function setParam(key: string, value: ParamValue) {
    setOverrides((o) => ({ ...o, [model]: { ...(o[model] || {}), [key]: value } }));
  }

  // Stop cancels everything: the server quits at the next sentence, nothing reaches History, and no audio is kept.
  function onStop() {
    abortRef.current?.abort();
  }

  // Leaving the page mid-take cancels it too, so it can't finish unseen and land in History.
  useEffect(() => () => abortRef.current?.abort(), []);

  async function onGenerate() {
    setError("");
    setBusy(true);
    const abort = new AbortController();
    abortRef.current = abort;
    try {
      const wanted = /^\d+$/.test(seedText.trim()) ? Number(seedText.trim()) : null;
      const args = { text, voice, model, language, params: values, pauseMs, save: keep, seed: wanted, signal: abort.signal };
      const streamed = stream && canStream(text);
      const { blob, duration, seed } = streamed
        ? await synthesizeStreaming(args, () => setHearing(true))
        : await synthesize(args);
      setLastSeed(seed);
      setAudioUrl((old) => {
        if (old) URL.revokeObjectURL(old);
        return URL.createObjectURL(blob);
      });
      setAudioBlob(blob);
      setAudioLength(duration);
      refreshModels().catch(() => {});
      if (keep) refreshGenerations().catch(() => {});
    } catch (e) {
      if (!abort.signal.aborted) setError(errorMessage(e));
    } finally {
      if (abortRef.current === abort) abortRef.current = null;
      setBusy(false);
      setHearing(false);
    }
  }

  async function onDownload() {
    if (!audioBlob) return;
    setError("");
    setConverting(true);
    try {
      saveBlob(await convertWav(audioBlob, format), `speech.${format}`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setConverting(false);
    }
  }

  return (
    <section>
      <div className="row">
        <label>
          Model
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
                {STATE_LABEL[m.state] ? ` (${STATE_LABEL[m.state]})` : ""}
              </option>
            ))}
          </select>
        </label>
        {selected?.multilingual && (
          <label>
            Language
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {LANGUAGES.map(([code, name]) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="row">
        <label>
          {voices.length ? "Voice (used until a {tag} switches it)" : "Voice"}
          <select value={voice} onChange={(e) => setVoice(e.target.value)}>
            {showBuiltin && <option value="">Built-in voice</option>}
            {voices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        {multi && (
          <label>
            Pause between voices: {pauseMs} ms
            <input type="range" min="0" max="2000" step="50" value={pauseMs} onChange={(e) => setPauseMs(parseInt(e.target.value, 10))} />
          </label>
        )}
      </div>
      <TagTextarea
        rows={8}
        placeholder="Type something for Chatterbox to say. Type { for voices (put {cut} inside a line to chop it off mid-phrase, {cut 200} to let the next voice talk over it by 200 ms). On Turbo, type [ for sound tags."
        value={text}
        onChange={setText}
        tags={model === "turbo" || model === "nano" ? TURBO_TAGS : []}
        voiceNames={voices.map((v) => v.name)}
      />

      <AdvancedParams
        spec={selected?.params}
        values={values}
        onChange={setParam}
        onReset={() => setOverrides((o) => ({ ...o, [model]: {} }))}
      />

      {selected && !ready && (
        <p className="hint">
          {selected.state === "loading" && `${selected.label} is loading. Generation unlocks when it is ready.`}
          {selected.state === "idle" && `${selected.label} is not loaded yet.`}
          {selected.state === "error" && `${selected.label} failed to load: ${selected.error}`}{" "}
          {(selected.state === "idle" || selected.state === "error") && (
            <button type="button" onClick={onLoadModel}>
              {selected.state === "error" ? "Retry load" : "Load model"}
            </button>
          )}
        </p>
      )}
      <div className="row">
        <button onClick={onGenerate} disabled={!canGenerate}>
          {busy ? (hearing ? "Playing..." : "Generating...") : "Generate"}
        </button>
        {busy && (
          <button type="button" className="stop" onClick={onStop} title="Cancel this take. Nothing is kept, not even a partial one.">
            Stop
          </button>
        )}
        <label className="inline">
          <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
          Keep in History
        </label>
        <label
          className="inline"
          title={canStream(text) ? "Play each sentence as soon as it is ready." : "Scripts with {cut} are generated whole, then played."}
        >
          <input type="checkbox" checked={stream && canStream(text)} disabled={!canStream(text)} onChange={(e) => setStream(e.target.checked)} />
          Stream
        </label>
        <label className="inline">
          Seed
          <input
            type="text"
            inputMode="numeric"
            className="seed"
            placeholder="random"
            value={seedText}
            onChange={(e) => setSeedText(e.target.value.replace(/\D/g, "").slice(0, 10))}
            title="Leave empty for a new take each time. A number repeats the same take for the same text, voice and settings."
          />
        </label>
        {lastSeed !== null && String(lastSeed) !== seedText && (
          <button type="button" onClick={() => setSeedText(String(lastSeed))} title="Lock the seed so the next run repeats this take">
            Use seed {lastSeed}
          </button>
        )}
      </div>
      {audioUrl && (
        <div className="player">
          <Player src={audioUrl} duration={audioLength} />
          <FormatSelect value={format} onChange={setFormat} />
          {format === "wav" ? (
            <a href={audioUrl} download="speech.wav">
              Download
            </a>
          ) : (
            <button type="button" onClick={onDownload} disabled={converting}>
              {converting ? "Converting..." : "Download"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
