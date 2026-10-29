import { useState } from "react";
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

interface CardProps {
  generation: GenerationOut;
  onDelete: (id: string) => void;
}

function GenerationCard({ generation: g, onDelete }: CardProps) {
  const [open, setOpen] = useState(false);
  const url = `/api/generations/${encodeURIComponent(g.id)}/audio`;
  const extra = describeParams(g.params);
  const lines = g.segments?.length ?? 0;
  return (
    <li className={`gen-card${open ? " open" : ""}`}>
      <div className="gen-main">
        <span className="gen-meta">
          {g.model}
          {g.voice ? ` / ${g.voice}` : ""}
          {lines > 1 ? ` / ${lines} lines` : ""} / {new Date(g.created * 1000).toLocaleString()}
        </span>
        <button
          type="button"
          className="gen-toggle"
          aria-expanded={open}
          aria-label={open ? "Collapse script" : "Show full script"}
          onClick={() => setOpen(!open)}
        >
          <span className="chevron" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          <span className={open ? "gen-text" : "gen-text clamped"}>{g.text}</span>
        </button>
        {open && extra && <p className="hint">Settings: {extra}</p>}
      </div>
      <div className="gen-side">
        <audio controls preload="none" src={url} />
        <div className="gen-actions">
          <a className="button" href={url} download={`${g.id}.wav`}>
            Download
          </a>
          <button className="danger" onClick={() => onDelete(g.id)}>
            Delete
          </button>
        </div>
      </div>
    </li>
  );
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
      <p className="hint">Click the script on an entry to expand it and read everything that was sent to the model.</p>
      <ul className="gens-list">
        {generations.length === 0 && <li className="hint">Nothing here yet. Clips you generate with "Keep in History" on show up here.</li>}
        {generations.map((g) => (
          <GenerationCard key={g.id} generation={g} onDelete={onDelete} />
        ))}
      </ul>
    </section>
  );
}
