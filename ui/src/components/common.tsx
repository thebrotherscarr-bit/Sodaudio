import { useEffect, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";
import { get, qs } from "../api";
import { deck, useDeck } from "../player";
import { fmtDuration } from "../util";

export function PlayButton({ url, title }: { url: string; title?: string }) {
  const d = useDeck();
  const on = d.playing && d.url === url;
  return (
    <button className="icon" title={title || "Play"} onClick={(e) => { e.stopPropagation(); deck.toggle(url); }}>
      {on ? "■" : "▶"}
    </button>
  );
}

export function ErrorBox({ error }: { error: string | null | undefined }) {
  return error ? <div className="error">{error}</div> : null;
}

export function Progress({ value }: { value: number }) {
  return <div className="progress"><div style={{ width: `${Math.round((value || 0) * 100)}%` }} /></div>;
}

// A waveform of the shared deck's current file when it matches, so clicking seeks the take you are hearing.
export function Waveform({ url, height = 64 }: { url: string; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const ws = useRef<WaveSurfer | null>(null);
  const d = useDeck();
  useEffect(() => {
    if (!ref.current) return;
    const surfer = WaveSurfer.create({ container: ref.current, height, waveColor: "#4d7ea8", progressColor: "#6fc3ff", cursorColor: "#f0b450",
      url, interact: true, normalize: true, barWidth: 0 });
    surfer.on("interaction", (t: number) => {
      if (deck.url === url) deck.seek(t);
      else deck.play(url, { from: t });
    });
    ws.current = surfer;
    return () => surfer.destroy();
  }, [url, height]);
  useEffect(() => {
    const surfer = ws.current;
    if (surfer && d.url === url && surfer.getDuration()) surfer.setTime(d.audio.currentTime);
  });
  return <div className="wave" ref={ref} />;
}

// The engine's folder browser (PWA: a browser cannot learn real paths, so the engine lists folders for us).
export function FolderBrowser({ mode, onPick, onClose, start }: { mode: "folder" | "file"; onPick: (path: string) => void; onClose: () => void; start?: string }) {
  const [listing, setListing] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const open = (path = "") => get(`/fs${qs({ path })}`).then((l) => { setListing(l); setError(null); }).catch((e) => setError(e.message));
  useEffect(() => { open(start || ""); }, []);
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row"><b>{mode === "folder" ? "Pick a folder" : "Pick a file"}</b><span className="spacer" /><button onClick={onClose}>Close</button></div>
        <div className="row mono muted">{listing?.path || "Allowed folders"}</div>
        <ErrorBox error={error} />
        <div className="list">
          {listing?.path && <div onClick={() => open(listing.parent || "")}>⬑ ..</div>}
          {listing?.folders.map((f: any) => <div key={f.path} onClick={() => open(f.path)}>📁 {f.name}</div>)}
          {mode === "file" && listing?.files.map((f: any) => (
            <div key={f.path} onClick={() => onPick(f.path)}>📄 {f.name} <span className="muted">{(f.size / 1024).toFixed(0)} KB</span></div>
          ))}
        </div>
        {mode === "folder" && <div className="row"><span className="spacer" /><button className="primary" disabled={!listing?.path} onClick={() => onPick(listing.path)}>Use this folder</button></div>}
      </div>
    </div>
  );
}

export function Eta({ seconds }: { seconds?: number | null }) {
  return seconds ? <span className="muted">about {fmtDuration(seconds)} left</span> : null;
}
