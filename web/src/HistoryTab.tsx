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
      <p className="hint">Hover a line to see the full text that produced it.</p>
      <ul className="voices gens">
        {generations.length === 0 && <li className="hint">Nothing here yet. Clips you generate with "Keep in History" on show up here.</li>}
        {generations.map((g) => {
          const url = `/api/generations/${encodeURIComponent(g.id)}/audio`;
          const extra = describeParams(g.params);
          return (
            <li key={g.id}>
              <span className="gen-text" title={extra ? `${g.text}\n\n${extra}` : g.text}>
                {g.text}
              </span>
              <span className="gen-meta">
                {g.model}
                {g.voice ? ` / ${g.voice}` : ""}
                {(g.segments?.length ?? 0) > 1 ? ` / ${g.segments?.length} lines` : ""} / {new Date(g.created * 1000).toLocaleString()}
              </span>
              <audio controls preload="none" src={url} />
              <a href={url} download={`${g.id}.wav`}>
                Download
              </a>
              <button className="danger" onClick={() => onDelete(g.id)}>
                Delete
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
