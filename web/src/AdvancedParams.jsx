// Generation settings for one model variant, built from the spec the API reports.
// `values` holds only the user's overrides; anything missing uses the library default.
export default function AdvancedParams({ spec, values, onChange, onReset }) {
  const keys = Object.keys(spec || {});
  if (keys.length === 0) return null;
  return (
    <details className="advanced">
      <summary>Advanced settings{Object.keys(values).length > 0 ? ` (${Object.keys(values).length} changed)` : ""}</summary>
      <div className="param-grid">
        {keys.map((key) => {
          const s = spec[key];
          const current = values[key] ?? s.default;
          return (
            <label key={key} className="param" title={s.help}>
              <span>
                {s.label}
                {s.type !== "bool" && <output>{current}</output>}
              </span>
              {s.type === "bool" ? (
                <input type="checkbox" checked={current} onChange={(e) => onChange(key, e.target.checked)} />
              ) : (
                <input
                  type="range"
                  min={s.min}
                  max={s.max}
                  step={s.step}
                  value={current}
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
