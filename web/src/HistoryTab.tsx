import { useState } from "react";
import { deleteGeneration, errorMessage } from "./api";
import type { GenerationOut } from "./api";
import { generationAudioUrl } from "./api";
import { FormatSelect, useExportFormat } from "./ExportFormat";
import Player from "./Player";

interface Props {
  generations: GenerationOut[];
  onReuse: (g: GenerationOut) => void;
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
  onReuse: (g: GenerationOut) => void;
  format: string;
  setFormat: (format: string) => void;
}

function GenerationCard({ generation: g, onDelete, onReuse, format, setFormat }: CardProps) {
  const [open, setOpen] = useState(false);
  const url = generationAudioUrl(g.id);
  const extra = describeParams(g.params);
  const lines = g.segments?.length ?? 0;
  return (
    <li className={`gen-card${open ? " open" : ""}`}>
      <div className="gen-main">
        <span className="gen-meta">
          {g.model}
          {g.voice ? ` / ${g.voice}` : ""}
          {lines > 1 ? ` / ${lines} lines` : ""}
          {g.seed != null ? ` / seed ${g.seed}` : ""} / {new Date(g.created * 1000).toLocaleString()}
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
        <Player src={url} duration={g.duration} />
        <div className="gen-actions">
          <FormatSelect value={format} onChange={setFormat} />
          <a className="button" href={generationAudioUrl(g.id, format, true)} download={`${g.id}.${format}`}>
            Download
          </a>
          <button type="button" onClick={() => onReuse(g)} title="Load this entry's settings and seed into Generate">
            Reuse
          </button>
          <button className="danger" onClick={() => onDelete(g.id)}>
            Delete
          </button>
        </div>
      </div>
    </li>
  );
}

export default function HistoryTab({ generations, onReuse, refreshGenerations, setError }: Props) {
  const [format, setFormat] = useExportFormat();
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
          <GenerationCard key={g.id} generation={g} onDelete={onDelete} onReuse={onReuse} format={format} setFormat={setFormat} />
        ))}
      </ul>
    </section>
  );
}
