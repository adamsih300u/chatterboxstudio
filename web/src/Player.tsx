import { useEffect, useRef, useState } from "react";
import { formatDuration } from "./audioEdit.js";

interface Props {
  src: string;
  // Length in seconds when the server already knows it; otherwise it is read from the file.
  duration?: number | null;
  autoPlay?: boolean;
}

// A compact player: play/pause, a short seek bar and "elapsed / total". The audio element loads
// its metadata up front and the server's length is used until it arrives, so the total is never 0:00.
export default function Player({ src, duration, autoPlay }: Props) {
  const ref = useRef<HTMLAudioElement>(null);
  const [loaded, setLoaded] = useState<number | null>(null);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    setLoaded(null);
    setTime(0);
    setPlaying(false);
  }, [src]);
  const total = duration ?? loaded;
  const known = total != null && Number.isFinite(total) && total > 0;

  const toggle = () => {
    const a = ref.current;
    if (!a) return;
    if (a.paused) void a.play().catch(() => setPlaying(false));
    else a.pause();
  };

  return (
    <span className="player-compact">
      <audio
        ref={ref}
        preload="metadata"
        autoPlay={autoPlay}
        src={src}
        onLoadedMetadata={(e) => setLoaded(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : null)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false);
          setTime(0);
        }}
      />
      <button type="button" className="player-btn" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
        {playing ? "⏸" : "▶"}
      </button>
      <input
        type="range"
        className="player-seek"
        aria-label="Seek"
        min={0}
        max={known ? total : 0}
        step="any"
        value={known ? Math.min(time, total) : 0}
        disabled={!known}
        onChange={(e) => {
          const a = ref.current;
          if (a) a.currentTime = Number(e.target.value);
          setTime(Number(e.target.value));
        }}
      />
      <span className="player-time">
        {formatDuration(time) || "0:00"} / {known ? formatDuration(total) : "--:--"}
      </span>
    </span>
  );
}
