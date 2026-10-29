import { useEffect, useState } from "react";
import AdvancedParams from "./AdvancedParams";
import type { ParamValue, ParamValues } from "./AdvancedParams";
import TagTextarea, { TURBO_TAGS } from "./TagTextarea";
import { errorMessage, synthesize } from "./api";
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

interface Props {
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

export default function GenerateTab({ models, model, setModel, voices, defaultVoice, builtinHidden, onLoadModel, refreshModels, refreshGenerations, setError }: Props) {
  const [text, setText] = useState("");
  const [voice, setVoice] = useState("");
  const [language, setLanguage] = useState("en");
  // Overrides per variant, so switching models does not leak settings across.
  const [overrides, setOverrides] = useState<Record<string, ParamValues>>({});
  const [pauseMs, setPauseMs] = useState(300);
  const [keep, setKeep] = useState(true);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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

  async function onGenerate() {
    setError("");
    setBusy(true);
    try {
      const { blob } = await synthesize({ text, voice, model, language, params: values, pauseMs, save: keep });
      setAudioUrl((old) => {
        if (old) URL.revokeObjectURL(old);
        return URL.createObjectURL(blob);
      });
      refreshModels().catch(() => {});
      if (keep) refreshGenerations().catch(() => {});
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
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
        tags={model === "turbo" ? TURBO_TAGS : []}
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
          {busy ? "Generating..." : "Generate"}
        </button>
        <label className="inline">
          <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
          Keep in History
        </label>
      </div>
      {audioUrl && (
        <div className="player">
          <audio controls src={audioUrl} />
          <a href={audioUrl} download="speech.wav">
            Download
          </a>
        </div>
      )}
    </section>
  );
}
