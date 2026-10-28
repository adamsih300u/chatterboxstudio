import { useEffect, useState } from "react";
import GenerateTab from "./GenerateTab.jsx";
import HistoryTab from "./HistoryTab.jsx";
import VoicesTab from "./VoicesTab.jsx";
import { getGenerations, getModels, getVoices, loadModel } from "./api.js";

const TABS = [
  ["generate", "Generate"],
  ["voices", "Voices"],
  ["history", "History"],
];

function tabFromHash() {
  const t = window.location.hash.slice(1);
  return TABS.some(([id]) => id === t) ? t : "generate";
}

export default function App() {
  const [tab, setTab] = useState(tabFromHash);
  const [models, setModels] = useState([]);
  const [model, setModel] = useState("");
  const [voices, setVoices] = useState([]);
  const [defaultVoice, setDefaultVoice] = useState("");
  const [builtinHidden, setBuiltinHidden] = useState(false);
  const [generations, setGenerations] = useState([]);
  const [error, setError] = useState("");

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
    Promise.all([refreshModels(), refreshVoices(), refreshGenerations()]).catch((e) => setError(e.message));
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
      setError(e.message);
    }
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
        />
      </div>
      {tab === "voices" && <VoicesTab voices={voices} defaultVoice={defaultVoice} builtinHidden={builtinHidden} refreshVoices={refreshVoices} setError={setError} />}
      {tab === "history" && (
        <HistoryTab generations={generations} refreshGenerations={refreshGenerations} setError={setError} />
      )}
    </main>
  );
}
