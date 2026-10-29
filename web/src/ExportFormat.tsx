import { useState } from "react";

export const EXPORT_FORMATS: [string, string][] = [
  ["wav", "WAV"],
  ["mp3", "MP3"],
  ["m4a", "M4A"],
  ["m4r", "M4R (ringtone, 40 s max)"],
];

const KEY = "exportFormat";

// The chosen download format, remembered between visits and shared by every tab.
export function useExportFormat(): [string, (format: string) => void] {
  const [format, setFormat] = useState(() => {
    const saved = localStorage.getItem(KEY) ?? "";
    return EXPORT_FORMATS.some(([id]) => id === saved) ? saved : "wav";
  });
  return [
    format,
    (next) => {
      localStorage.setItem(KEY, next);
      setFormat(next);
    },
  ];
}

interface Props {
  value: string;
  onChange: (format: string) => void;
  label?: string;
}

export function FormatSelect({ value, onChange, label = "Download as" }: Props) {
  return (
    <select aria-label={label} title={label} className="format-select" value={value} onChange={(e) => onChange(e.target.value)}>
      {EXPORT_FORMATS.map(([id, name]) => (
        <option key={id} value={id}>
          {name}
        </option>
      ))}
    </select>
  );
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
