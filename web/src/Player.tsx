import { useEffect, useState } from "react";
import { formatDuration } from "./audioEdit.js";

interface Props {
  src: string;
  // Length in seconds when the server already knows it; otherwise it is read from the file.
  duration?: number | null;
  autoPlay?: boolean;
}

// An audio element that loads its metadata up front (so the browser's own bar shows the total
// instead of 0:00) next to a length label that is right even before that arrives.
export default function Player({ src, duration, autoPlay }: Props) {
  const [loaded, setLoaded] = useState<number | null>(null);
  useEffect(() => setLoaded(null), [src]);
  const shown = duration ?? loaded;
  return (
    <span className="player-with-length">
      <audio
        controls
        preload="metadata"
        autoPlay={autoPlay}
        src={src}
        onLoadedMetadata={(e) => setLoaded(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : null)}
      />
      {shown != null && formatDuration(shown) && (
        <span className="duration" title="Length">
          {formatDuration(shown)}
        </span>
      )}
    </span>
  );
}
