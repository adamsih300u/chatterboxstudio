// Generation settings for one model variant, built from the spec the API reports.
// `values` holds only the user's overrides; anything missing uses the library default.
import type { ParamSpec } from "./api";

export type ParamValue = number | boolean;
export type ParamValues = Record<string, ParamValue>;

interface Props {
  spec: Record<string, ParamSpec> | undefined;
  values: ParamValues;
  onChange: (key: string, value: ParamValue) => void;
  onReset: () => void;
}

export default function AdvancedParams({ spec, values, onChange, onReset }: Props) {
  if (!spec) return null;
  const keys = Object.keys(spec);
  if (keys.length === 0) return null;
  return (
    <details className="advanced">
      <summary>Advanced settings{Object.keys(values).length > 0 ? ` (${Object.keys(values).length} changed)` : ""}</summary>
      <div className="param-grid">
        {keys.map((key) => {
          const s = spec[key];
          const current = values[key] ?? (s.default as ParamValue);
          return (
            <label key={key} className="param" title={s.help}>
              <span>
                {s.label}
                {s.type !== "bool" && <output>{String(current)}</output>}
              </span>
              {s.type === "bool" ? (
                <input type="checkbox" checked={Boolean(current)} onChange={(e) => onChange(key, e.target.checked)} />
              ) : (
                <input
                  type="range"
                  min={s.min ?? undefined}
                  max={s.max ?? undefined}
                  step={s.step ?? undefined}
                  value={Number(current)}
                  onChange={(e) => onChange(key, s.type === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value))}
                />
              )}
              <small>{s.help}</small>
            </label>
          );
        })}
      </div>
      <button type="button" onClick={onReset} disabled={Object.keys(values).length === 0}>
        Reset to defaults
      </button>
    </details>
  );
}
