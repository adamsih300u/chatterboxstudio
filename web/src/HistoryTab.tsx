import { deleteGeneration, errorMessage } from "./api";
import type { GenerationOut } from "./api";

interface Props {
  generations: GenerationOut[];
  refreshGenerations: () => Promise<void>;
  setError: (message: string) => void;
}

function describeParams(params: GenerationOut["params"]) {
  const entries = Object.entries(params || {});
  return entries.length ? entries.map(([k, v]) => `${k}=${v}`).join(", ") : "";
}

export default function HistoryTab({ generations, refreshGenerations, setError }: Props) {
  async function onDelete(id: string) {
    setError("");
    try {
      await deleteGeneration(id);
      await refreshGenerations();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <section>
      <p className="hint">Click "Show full script" on an entry to read everything that was sent to the model.</p>
      <ul className="gens-list">
        {generations.length === 0 && <li className="hint">Nothing here yet. Clips you generate with "Keep in History" on show up here.</li>}
        {generations.map((g) => {
          const url = `/api/generations/${encodeURIComponent(g.id)}/audio`;
          const extra = describeParams(g.params);
          const lines = g.segments?.length ?? 0;
          return (
            <li key={g.id} className="gen-card">
              <div className="gen-head">
                <span className="gen-meta">
                  {g.model}
                  {g.voice ? ` / ${g.voice}` : ""}
                  {lines > 1 ? ` / ${lines} lines` : ""} / {new Date(g.created * 1000).toLocaleString()}
                </span>
                <a href={url} download={`${g.id}.wav`}>
                  Download
                </a>
                <button className="danger" onClick={() => onDelete(g.id)}>
                  Delete
                </button>
              </div>
              <p className="gen-preview">{g.text}</p>
              <audio controls preload="none" src={url} />
              <details className="gen-script">
                <summary>Show full script</summary>
                <pre className="gen-full" tabIndex={0}>
                  {g.text}
                </pre>
                {extra && <p className="hint">Settings: {extra}</p>}
              </details>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
