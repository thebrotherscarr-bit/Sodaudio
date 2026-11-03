import { useEffect, useMemo, useRef, useState } from "react";
import { API, fileUrl, get, MapData, post, qs, upload, waitForJob } from "../api";
import { nav } from "../App";
import { ErrorBox, FolderBrowser } from "../components/common";
import { native } from "../native";
import { deck, useDeck } from "../player";
import { fmtTime } from "../util";

interface Piece { from: number; to: number; gain_db: number }
type Grid = MapData & { bar_phase?: number };
const ROWS = { bars: 16, loud: 70, mel: 150, band: 80, chroma: 90 };
const HEIGHT = ROWS.bars + ROWS.loud + ROWS.mel + ROWS.band + ROWS.chroma;

function heat(v: number) { // 0..1 -> magma-like colour
  const r = Math.min(255, Math.round(255 * Math.min(1, v * 1.6)));
  const g = Math.round(255 * Math.max(0, v - 0.45) * 1.6);
  const b = Math.round(120 * Math.sin(Math.PI * Math.min(1, v * 1.2)) + 40 * v);
  return [r, g, b];
}

export default function MapScreen({ initial }: { initial?: string }) {
  const [file, setFile] = useState<string>(initial || localStorage.getItem("mapFile") || "");
  const [input, setInput] = useState(file);
  const [map, setMap] = useState<MapData | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(8);
  const [sel, setSel] = useState<[number, number] | null>(null);
  const [pieces, setPieces] = useState<Piece[]>([]);
  const [ring, setRing] = useState<number | "">("");
  const [loopMin, setLoopMin] = useState(30);
  const [loopMax, setLoopMax] = useState(45);
  const [browse, setBrowse] = useState(false);
  const [over, setOver] = useState(false);
  const [shift, setShift] = useState(0);

  useEffect(() => { if (initial) { setFile(initial); setInput(initial); } }, [initial]);
  useEffect(() => {
    if (!file) return;
    localStorage.setItem("mapFile", file);
    setMap(null); setSel(null); setError(null); setStatus("Mapping…");
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`${API}/maps${qs({ file })}`);
        let data = await response.json();
        if (response.status === 202) {
          const job = await waitForJob(data.job);
          if (job.state !== "done") throw new Error(job.error || "mapping failed");
          data = await get(`/maps${qs({ file })}`);
        } else if (!response.ok) throw new Error(data.detail);
        if (!cancelled) { setShift(0); setMap(data); setStatus(null); setZoom(Math.max(2, Math.min(30, (window.innerWidth - 600) / data.duration))); }
      } catch (e: any) { if (!cancelled) { setError(e.message); setStatus(null); } }
    })();
    return () => { cancelled = true; };
  }, [file]);

  const url = file ? fileUrl(file) : "";
  const playing = useDeckPlaying(url);
  const take = file;
  const beat = map ? 60 / map.tempo : 0.5;
  // Bars from the beat list with the downbeat moved by `shift` beats (the tracker can pick the wrong beat as "one").
  const grid: MapData | null = useMemo(() => {
    if (!map) return null;
    const meter = map.meter || 4, phase = ((map as Grid).bar_phase ?? 0) + shift;
    return { ...map, bars: map.beats.filter((_, i) => (((i - phase) % meter) + meter) % meter === 0) };
  }, [map, shift]);

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault(); setOver(false);
    const dropped = e.dataTransfer.files[0];
    if (!dropped) return;
    const n = native();
    // Electron: the dropped file's real path, mapped in place. Browser: uploaded to the workspace inbox.
    if (n) { const path = n.pathForFile(dropped); setFile(path); setInput(path); }
    else { setStatus("Uploading…"); const r = await upload(dropped); setFile(r.file); setInput(r.file); }
  };

  const findLoop = async () => {
    const r = await post("/jobs", { type: "loop", take, min: loopMin, max: loopMax, ...(sel ? { include: sel } : {}) });
    setStatus("Finding a loop…");
    const job = await waitForJob(r.ids[0]);
    setStatus(job.state === "done" ? `Made ${job.result.candidate}: ${job.result.seconds.toFixed(1)} s, ${job.result.bars} bars from ${job.result.start.toFixed(2)} s (match ${job.result.match}). It is in Audition.` : null);
    if (job.state !== "done") setError(job.error);
  };
  const render = async () => {
    const r = await post("/jobs", { type: "recompose", take, pieces, ...(ring !== "" ? { ring } : {}) });
    setStatus("Rendering…");
    const job = await waitForJob(r.ids[0]);
    setStatus(job.state === "done" ? `Made ${job.result.candidate}: ${job.result.seconds.toFixed(2)} s. It is in Audition.` : null);
    if (job.state !== "done") setError(job.error);
  };
  const exportPng = async () => {
    const response = await fetch(`${API}/maps/png${qs({ file })}`);
    if (response.status === 202) { setStatus("Drawing the picture…"); await waitForJob((await response.json()).job); setStatus(null); }
    window.open(`${API}/maps/png${qs({ file })}`, "_blank");
  };

  return (
    <div onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}>
      <div className="row" style={{ marginBottom: 8 }}>
        <h1 style={{ margin: 0 }}>Map & recompose</h1>
        {map && <span className="muted">{map.name || file.split(/[\\/]/).pop()}: {map.bars.length} bars, measured {map.tempo.toFixed(1)} BPM{map.asked_bpm ? ` (asked ${map.asked_bpm})` : ""}, {map.key}, {fmtTime(map.duration)}</span>}
      </div>
      <div className="row">
        <input className="grow mono" value={input} onChange={(e) => setInput(e.target.value)} placeholder="a take (mus_title__c8), a workspace path, game:Assets/Music/x.ogg, or drop a file here" />
        <button className="primary" onClick={() => setFile(input.trim())}>Map</button>
        <button onClick={async () => { const n = native(); if (n) { const p = await n.pickFile([{ name: "Audio", extensions: ["flac", "wav", "ogg", "mp3"] }]); if (p) { setFile(p); setInput(p); } } else setBrowse(true); }}>Pick file…</button>
        {map && <button onClick={exportPng}>Export PNG</button>}
      </div>
      <div className={`drop ${over ? "over" : ""}`} style={{ margin: "8px 0", padding: 8 }}>Drop an audio file anywhere here to map it</div>
      <ErrorBox error={error} />
      {status && <div className="card" style={{ marginBottom: 8 }}>{status}</div>}
      {map && (
        <>
          <div className="row" style={{ marginBottom: 6 }}>
            <button onClick={() => deck.toggle(url)}>{playing ? "Stop" : "Play"}</button>
            <label>Zoom</label><input type="range" min={2} max={40} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} />
            <span className="muted">Click to play from a point; drag to select bars (hold Alt for no snapping); double-click clears.</span>
            <label>Downbeat</label>
            <button className="small" onClick={() => setShift(shift - 1)} title="Move bar lines one beat earlier">◀ beat</button>
            <button className="small" onClick={() => setShift(shift + 1)} title="Move bar lines one beat later">beat ▶</button>
            {sel && <span className="pill warm">selected {sel[0].toFixed(2)}–{sel[1].toFixed(2)} s ({barsIn(grid!, sel)} bars)</span>}
          </div>
          <MapCanvas map={grid!} url={url} zoom={zoom} sel={sel} setSel={setSel} pieces={pieces} />
          <div className="row" style={{ marginTop: 10 }}>
            <b>Find a loop</b>
            <label>min</label><input type="number" value={loopMin} onChange={(e) => setLoopMin(Number(e.target.value))} style={{ width: 60 }} />
            <label>max s</label><input type="number" value={loopMax} onChange={(e) => setLoopMax(Number(e.target.value))} style={{ width: 60 }} />
            <button className="primary" onClick={findLoop}>{sel ? "Find a loop here (keeps the selection)" : "Find a loop"}</button>
          </div>
          <h2>Recompose</h2>
          <div className="row" style={{ marginBottom: 6 }}>
            <button disabled={!sel} onClick={() => sel && setPieces([...pieces, { from: sel[0], to: sel[1], gain_db: 0 }])}>Add selection as a piece</button>
            <label>Ring-out ends at (s in the take)</label>
            <input type="number" step="0.01" value={ring} onChange={(e) => setRing(e.target.value === "" ? "" : Number(e.target.value))} style={{ width: 90 }} />
            <span className="muted">Joins are equal-power crossfades a quarter beat long ({(beat / 4).toFixed(3)} s).</span>
          </div>
          <div className="strip">
            {pieces.map((p, i) => (
              <div key={i} className="piece">
                <input type="number" step="0.01" value={p.from} style={{ width: 76 }} title="from (s in the take)"
                  onChange={(e) => setPieces(pieces.map((q, j) => (j === i ? { ...q, from: Number(e.target.value) } : q)))} />
                <span>–</span>
                <input type="number" step="0.01" value={p.to} style={{ width: 76 }} title="to (s in the take)"
                  onChange={(e) => setPieces(pieces.map((q, j) => (j === i ? { ...q, to: Number(e.target.value) } : q)))} />
                <label>dB</label>
                <input type="number" step="0.5" value={p.gain_db} style={{ width: 60 }}
                  onChange={(e) => setPieces(pieces.map((q, j) => (j === i ? { ...q, gain_db: Number(e.target.value) } : q)))} />
                <button className="small" disabled={i === 0} onClick={() => { const n = [...pieces]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; setPieces(n); }}>◀</button>
                <button className="small" disabled={i === pieces.length - 1} onClick={() => { const n = [...pieces]; [n[i + 1], n[i]] = [n[i], n[i + 1]]; setPieces(n); }}>▶</button>
                <button className="small danger" onClick={() => setPieces(pieces.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
            {!pieces.length && <span className="muted">Select bars on the map and add them here, in playing order.</span>}
          </div>
          <div className="row" style={{ marginTop: 6 }}>
            <StripPlayer url={url} pieces={pieces} fade={beat / 4} ring={ring === "" ? null : ring} />
            <span className="muted">{pieces.length ? `${pieces.reduce((s, p) => s + p.to - p.from, 0).toFixed(2)} s` : ""}</span>
            <button className="primary" disabled={!pieces.length} onClick={render}>Render</button>
            <button onClick={() => nav.go("Audition")}>Open Audition</button>
          </div>
        </>
      )}
      {browse && <FolderBrowser mode="file" onClose={() => setBrowse(false)} onPick={(p) => { setBrowse(false); setFile(p); setInput(p); }} />}
    </div>
  );
}

function useDeckPlaying(url: string) {
  const d = useDeck();
  return d.playing && d.url === url;
}

function barsIn(map: MapData, sel: [number, number]) {
  return map.bars.filter((b) => b >= sel[0] - 0.01 && b < sel[1] - 0.01).length;
}

function snap(map: MapData, t: number, up: boolean) {
  const edges = [...map.bars, map.duration];
  if (up) return edges.find((b) => b >= t - 0.001) ?? map.duration;
  return [...edges].reverse().find((b) => b <= t + 0.001) ?? 0;
}

function MapCanvas({ map, url, zoom, sel, setSel, pieces }: { map: MapData; url: string; zoom: number; sel: [number, number] | null;
  setSel: (s: [number, number] | null) => void; pieces: Piece[] }) {
  const base = useRef<HTMLCanvasElement>(null);
  const top = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{ x: number; t: number } | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const width = Math.max(600, Math.round(map.duration * zoom));
  const px = (t: number) => (t / map.duration) * width;

  // The static layers: loudness, mel spectrogram, choir band against drums, chroma, bar lines.
  useEffect(() => {
    const c = base.current!;
    c.width = width; c.height = HEIGHT;
    const g = c.getContext("2d")!;
    g.fillStyle = "#0d1015"; g.fillRect(0, 0, width, HEIGHT);
    const n = map.loudness.length;
    const fx = (i: number) => (i / n) * width;
    const line = (values: number[], y0: number, h: number, color: string, lo: number, hi: number) => {
      g.strokeStyle = color; g.lineWidth = 1; g.beginPath();
      values.forEach((v, i) => { const y = y0 + h - ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * h; i ? g.lineTo(fx(i), y) : g.moveTo(fx(i), y); });
      g.stroke();
    };
    let y = ROWS.bars;
    line(map.loudness, y, ROWS.loud, "#6fc3ff", -60, 0);
    y += ROWS.loud;
    const image = (frames: number[][], y0: number, h: number, norm: (v: number) => number) => {
      const bins = frames[0]?.length || 1;
      const img = g.createImageData(width, h);
      for (let x = 0; x < width; x++) {
        const f = frames[Math.min(frames.length - 1, Math.floor((x / width) * frames.length))];
        for (let yy = 0; yy < h; yy++) {
          const bin = Math.floor(((h - 1 - yy) / h) * bins);
          const [r, gg, b] = heat(norm(f[bin]));
          const o = (yy * width + x) * 4;
          img.data[o] = r; img.data[o + 1] = gg; img.data[o + 2] = b; img.data[o + 3] = 255;
        }
      }
      g.putImageData(img, 0, y0);
    };
    image(map.mel.frames, y, ROWS.mel, (v) => Math.max(0, Math.min(1, (v + 80) / 80)));
    y += ROWS.mel;
    const lo = Math.min(...map.low, ...map.drums), hi = Math.max(...map.low, ...map.drums);
    line(map.low, y, ROWS.band, "#f0b450", lo, hi);
    line(map.drums, y, ROWS.band, "#7fd38f", lo, hi);
    g.fillStyle = "#f0b450"; g.font = "10px sans-serif"; g.fillText("choir 80–500 Hz", 4, y + 11);
    g.fillStyle = "#7fd38f"; g.fillText("drums", 100, y + 11);
    y += ROWS.band;
    image(map.chroma, y, ROWS.chroma, (v) => v);
    g.fillStyle = "#8d96a3"; g.fillText("loudness", 4, ROWS.bars + 11); g.fillText("mel", 4, ROWS.bars + ROWS.loud + 11); g.fillText("chroma", 4, y + 11);
    map.bars.forEach((b, i) => {
      const x = Math.round(px(b)) + 0.5;
      g.strokeStyle = "rgba(255,255,255,0.28)"; g.beginPath(); g.moveTo(x, ROWS.bars); g.lineTo(x, HEIGHT); g.stroke();
      g.fillStyle = "#c7cdd6"; g.fillText(String(i + 1), x + 2, 11);
    });
  }, [map, width]);

  // The moving layers: selection, pieces in the strip, hover and the playhead.
  useEffect(() => {
    let frame = 0;
    const draw = () => {
      const c = top.current!;
      if (c.width !== width) { c.width = width; c.height = HEIGHT; }
      const g = c.getContext("2d")!;
      g.clearRect(0, 0, width, HEIGHT);
      pieces.forEach((p) => { g.fillStyle = "rgba(127,211,143,0.16)"; g.fillRect(px(p.from), ROWS.bars, px(p.to) - px(p.from), HEIGHT); });
      if (sel) { g.fillStyle = "rgba(240,180,80,0.22)"; g.fillRect(px(sel[0]), 0, px(sel[1]) - px(sel[0]), HEIGHT); }
      if (hover !== null) { g.fillStyle = "#fff"; g.fillRect(px(hover), 0, 1, HEIGHT); g.font = "11px sans-serif"; g.fillText(fmtTime(hover), px(hover) + 4, HEIGHT - 4); }
      if (deck.url === url) { g.fillStyle = "#ef6b6b"; g.fillRect(px(deck.audio.currentTime), 0, 2, HEIGHT); }
      frame = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(frame);
  }, [width, sel, hover, pieces, url]);

  const timeAt = (e: React.MouseEvent) => {
    const rect = (e.target as HTMLCanvasElement).getBoundingClientRect();
    return Math.max(0, Math.min(map.duration, ((e.clientX - rect.left) / width) * map.duration));
  };
  return (
    <div className="mapwrap">
      <div style={{ position: "relative", width, height: HEIGHT }}>
        <canvas ref={base} style={{ position: "absolute", left: 0, top: 0 }} />
        <canvas ref={top} style={{ position: "absolute", left: 0, top: 0, cursor: "crosshair" }}
          onMouseDown={(e) => { drag.current = { x: e.clientX, t: timeAt(e) }; }}
          onMouseMove={(e) => {
            const t = timeAt(e);
            setHover(t);
            if (drag.current && Math.abs(e.clientX - drag.current.x) > 4) {
              const a = Math.min(drag.current.t, t), b = Math.max(drag.current.t, t);
              setSel(e.altKey ? [a, b] : [snap(map, a, false), snap(map, b, true)]);
            }
          }}
          onMouseUp={(e) => {
            if (drag.current && Math.abs(e.clientX - drag.current.x) <= 4) deck.play(url, { from: timeAt(e) });
            drag.current = null;
          }}
          onMouseLeave={() => { setHover(null); drag.current = null; }}
          onDoubleClick={() => setSel(null)} />
      </div>
    </div>
  );
}

// Plays the recompose strip as it will sound, looping, with the joins and the wrap-around (Web Audio).
function StripPlayer({ url, pieces, fade, ring }: { url: string; pieces: Piece[]; fade: number; ring: number | null }) {
  const ctx = useRef<AudioContext | null>(null);
  const buffer = useRef<{ url: string; data: AudioBuffer } | null>(null);
  const source = useRef<AudioBufferSourceNode | null>(null);
  const [playing, setPlaying] = useState(false);
  const key = useMemo(() => JSON.stringify([url, pieces, fade, ring]), [url, pieces, fade, ring]);
  useEffect(() => () => stop(), [key]);

  const stop = () => { source.current?.stop(); source.current = null; setPlaying(false); };
  const play = async () => {
    deck.stop();
    ctx.current ??= new AudioContext();
    const ac = ctx.current;
    if (buffer.current?.url !== url) buffer.current = { url, data: await ac.decodeAudioData(await (await fetch(url)).arrayBuffer()) };
    const src = buffer.current.data, rate = src.sampleRate, half = Math.floor((fade * rate) / 2);
    const chans = src.numberOfChannels;
    const parts = pieces.map((p) => ({ a: Math.floor(p.from * rate), b: Math.floor(p.to * rate), g: Math.pow(10, p.gain_db / 20) }));
    const length = parts.reduce((s, p) => s + p.b - p.a, 0);
    const out = ac.createBuffer(chans, length, rate);
    for (let c = 0; c < chans; c++) {
      const s = src.getChannelData(c), o = out.getChannelData(c);
      let at = 0;
      parts.forEach((p, i) => {
        // Same joins as the engine: each piece reaches half a fade past its cut, the next starts half a fade early.
        const start = i === 0 ? p.a : p.a - half, end = p.b + half;
        for (let k = start; k < end; k++) {
          const pos = at + (k - p.a);
          if (pos < 0 || pos >= length) continue;
          let w = 1;
          if (i > 0 && k < p.a + half) w = Math.sqrt((k - start) / (2 * half));
          if (i < parts.length - 1 && k >= p.b - half) w = Math.sqrt(Math.max(0, (end - k) / (2 * half)));
          o[pos] += (s[k] || 0) * p.g * w;
        }
        at += p.b - p.a;
      });
      if (ring) { const last = parts[parts.length - 1]; for (let k = last.b; k < Math.floor(ring * rate) && k - last.b < length; k++) o[k - last.b] += (s[k] || 0) * last.g; }
    }
    const node = ac.createBufferSource();
    node.buffer = out; node.loop = true; node.connect(ac.destination);
    node.start(0, Math.max(0, out.duration - 3)); // start just before the wrap-around
    source.current = node; setPlaying(true);
  };
  return <button disabled={!pieces.length} onClick={() => (playing ? stop() : play())}>{playing ? "Stop loop" : "Play loop (from the wrap)"}</button>;
}
