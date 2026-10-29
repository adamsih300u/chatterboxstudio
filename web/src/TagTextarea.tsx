import { useRef, useState } from "react";
import type { KeyboardEvent, TextareaHTMLAttributes } from "react";

// Paralinguistic tags in the Chatterbox Turbo tokenizer (added_tokens).
export const TURBO_TAGS = [
  "[laugh]", "[chuckle]", "[sigh]", "[gasp]", "[cough]",
  "[clear throat]", "[sniff]", "[groan]", "[shush]",
];

// {cut} ends the line: no pause, and the next voice starts immediately.
export const CUT_TAG = "{cut}";

// Find an unfinished "[xyz" or "{xyz" immediately before the caret.
interface OpenTag {
  start: number;
  kind: string;
  query: string;
}

function openTag(value: string, caret: number): OpenTag | null {
  const m = /([[{])([^[\]{}\n]*)$/.exec(value.slice(0, caret));
  return m ? { start: caret - m[0].length, kind: m[1], query: m[2].toLowerCase() } : null;
}

interface Props extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange"> {
  value: string;
  onChange: (value: string) => void;
  tags: string[];
  voiceNames?: string[];
}

export default function TagTextarea({ value, onChange, tags, voiceNames = [], ...rest }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [open, setOpen] = useState<OpenTag | null>(null);
  const [index, setIndex] = useState(0);

  const voiceTags = voiceNames.length ? [...voiceNames.map((n) => `{${n}}`), "{default}", CUT_TAG] : [];
  const pool = open?.kind === "{" ? voiceTags : tags;
  const matches = open ? pool.filter((t) => t.slice(1).toLowerCase().startsWith(open.query)) : [];

  function refresh(el: HTMLTextAreaElement) {
    const found = tags.length || voiceTags.length ? openTag(el.value, el.selectionStart) : null;
    setOpen(found);
    setIndex(0);
  }

  function insert(tag: string, start: number, end: number) {
    const next = value.slice(0, start) + tag + " " + value.slice(end);
    const caret = start + tag.length + 1;
    onChange(next);
    setOpen(null);
    requestAnimationFrame(() => {
      ref.current?.focus();
      ref.current?.setSelectionRange(caret, caret);
    });
  }

  function accept(tag: string) {
    if (!open || !ref.current) return;
    insert(tag, open.start, ref.current.selectionStart);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (!open || matches.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setIndex((i) => (i + 1) % matches.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setIndex((i) => (i - 1 + matches.length) % matches.length);
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      accept(matches[index]);
    } else if (e.key === "Escape") {
      setOpen(null);
    }
  }

  function insertAtCaret(tag: string) {
    const el = ref.current;
    if (!el) return;
    insert(tag, el.selectionStart, el.selectionEnd);
  }

  return (
    <div className="tag-textarea">
      <textarea
        {...rest}
        ref={ref}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          refresh(e.target);
        }}
        onKeyDown={onKeyDown}
        onKeyUp={(e) => {
          if (e.key.startsWith("Arrow") && !["ArrowUp", "ArrowDown"].includes(e.key)) refresh(e.currentTarget);
        }}
        onClick={(e) => refresh(e.currentTarget)}
        onBlur={() => setTimeout(() => setOpen(null), 120)}
      />
      {open && matches.length > 0 && (
        <ul className="tag-menu" role="listbox">
          {matches.map((t, i) => (
            <li
              key={t}
              role="option"
              aria-selected={i === index}
              className={i === index ? "active" : ""}
              onMouseDown={(e) => {
                e.preventDefault();
                accept(t);
              }}
            >
              {t}
            </li>
          ))}
        </ul>
      )}
      <div className="tag-inserts">
        {voiceNames.length > 0 && (
          <label className="tag-insert">
            Insert
            <select
              value=""
              onChange={(e) => {
                if (e.target.value) insertAtCaret(e.target.value);
              }}
            >
              <option value="">Voice tag...</option>
              {voiceNames.map((n) => (
                <option key={n} value={`{${n}}`}>
                  {`{${n}}`}
                </option>
              ))}
              <option value="{default}">{"{default}"}</option>
            </select>
          </label>
        )}
        {tags.length > 0 && (
          <label className="tag-insert">
            Insert
            <select
              value=""
              onChange={(e) => {
                if (e.target.value) insertAtCaret(e.target.value);
              }}
            >
              <option value="">Sound tag...</option>
              {tags.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
        )}
        <button
          type="button"
          className="tag-cut"
          title="Cut the line here: no pause, the next voice starts immediately"
          onClick={() => insertAtCaret(CUT_TAG)}
        >
          {CUT_TAG}
        </button>
      </div>
    </div>
  );
}
