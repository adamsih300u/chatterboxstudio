import { useEffect, useState } from "react";
import GenerateTab from "./GenerateTab";
import type { Reuse } from "./GenerateTab";
import type { ParamValues } from "./AdvancedParams";
import HistoryTab from "./HistoryTab";
import VoicesTab from "./VoicesTab";
import { errorMessage, getGenerations, getModels, getVoices, loadModel } from "./api";
import type { GenerationOut, ModelInfo, VoiceOut } from "./api";

type TabId = "generate" | "voices" | "history";

const TABS: [TabId, string][] = [
  ["generate", "Generate"],
  ["voices", "Voices"],
  ["history", "History"],
];

function tabFromHash(): TabId {
  const t = window.location.hash.slice(1);
  return TABS.find(([id]) => id === t)?.[0] ?? "generate";
}

export default function App() {
  const [tab, setTab] = useState(tabFromHash);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState("");
  const [voices, setVoices] = useState<VoiceOut[]>([]);
  const [defaultVoice, setDefaultVoice] = useState("");
  const [builtinHidden, setBuiltinHidden] = useState(false);
  const [generations, setGenerations] = useState<GenerationOut[]>([]);
  const [error, setError] = useState("");
  const [reuse, setReuse] = useState<Reuse | null>(null);

  const anyLoading = models.some((m) => m.state === "loading");

  async function refreshModels() {
    const data = await getModels();
    setModels(data.models);
    setModel((cur) => cur || data.default);
  }

  async function refreshVoices() {
    const data = await getVoices();
    setVoices(data.voices);
    setDefaultVoice(data.default || "");
    setBuiltinHidden(Boolean(data.builtin_hidden));
  }

  async function refreshGenerations() {
    setGenerations((await getGenerations()).generations);
  }

  useEffect(() => {
    Promise.all([refreshModels(), refreshVoices(), refreshGenerations()]).catch((e) => setError(errorMessage(e)));
  }, []);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Poll while any model is loading so the UI unlocks as soon as it is ready.
  useEffect(() => {
    if (!anyLoading) return undefined;
    const timer = setInterval(() => refreshModels().catch(() => {}), 2000);
    return () => clearInterval(timer);
  }, [anyLoading]);

  async function onLoadModel() {
    setError("");
    try {
      await loadModel(model);
      await refreshModels();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  // History's "Reuse" loads an entry's settings and seed into Generate and switches to it.
  function onReuse(g: GenerationOut) {
    const single = (g.segments?.length ?? 0) <= 1;
    const voice = single ? voices.find((v) => v.name === g.voice)?.id ?? "" : undefined;
    setReuse({ nonce: Date.now(), text: single ? g.text : undefined, model: g.model, voice, params: g.params as ParamValues, seed: g.seed });
    setModel(g.model);
    window.location.hash = "generate";
    setTab("generate");
  }

  return (
    <main>
      <h1>Chatterbox Studio</h1>
      <nav className="tabs" role="tablist">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? "active" : ""}
            onClick={() => {
              window.location.hash = id;
              setTab(id);
            }}
          >
            {label}
            {id === "voices" && voices.length > 0 ? ` (${voices.length})` : ""}
            {id === "history" && generations.length > 0 ? ` (${generations.length})` : ""}
          </button>
        ))}
      </nav>
      {error && <p className="error">{error}</p>}

      {/* Generate stays mounted so its text and settings survive tab switches. */}
      <div hidden={tab !== "generate"}>
        <GenerateTab
          models={models}
          model={model}
          setModel={setModel}
          voices={voices}
          defaultVoice={defaultVoice}
          builtinHidden={builtinHidden}
          onLoadModel={onLoadModel}
          refreshModels={refreshModels}
          refreshGenerations={refreshGenerations}
          setError={setError}
          reuse={reuse}
        />
      </div>
      {tab === "voices" && <VoicesTab model={model} voices={voices} defaultVoice={defaultVoice} builtinHidden={builtinHidden} refreshVoices={refreshVoices} setError={setError} />}
      {tab === "history" && (
        <HistoryTab generations={generations} refreshGenerations={refreshGenerations} setError={setError} onReuse={onReuse} />
      )}
    </main>
  );
}
