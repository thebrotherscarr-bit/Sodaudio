import { useEffect, useRef, useState } from "react";
import { get, Job, post } from "../api";
import { ErrorBox, Progress } from "../components/common";
import { useEvents } from "../events";
import { cx, fmtDuration, useLoad } from "../util";

function describe(job: Job) {
  const b = job.body || {};
  if (job.type === "generate") return `${(b.cues || []).slice(0, 3).join(", ")}${(b.cues || []).length > 3 ? ` +${b.cues.length - 3}` : ""}${b.takes ? ` → ${b.takes} takes` : ""}`;
  if (job.type === "post") return (b.takes || []).join(", ");
  if (job.type === "promote") return b.fill_gaps ? "fill gaps" : (b.candidates || []).join(", ");
  return b.take || b.file || b.candidate || (b.steps ? b.steps.join(", ") : "");
}

export default function QueueScreen({ initial }: { initial?: number }) {
  const { data, error, reload, setData } = useLoad(() => get<{ jobs: Job[]; paused: boolean; mode: string }>("/jobs"));
  const [selected, setSelected] = useState<number | null>(initial ?? null);
  const [tab, setTab] = useState<"job" | "comfy" | "engine" | "chart">(initial ? "job" : "job");
  const [dragging, setDragging] = useState<number | null>(null);
  const [eta, setEta] = useState<Record<number, number>>({});

  useEvents(["job", "queue"], (e) => {
    if (e.type === "job" && e.eta_s) setEta((m) => ({ ...m, [e.id]: e.eta_s }));
    if (e.type === "job" && e.state === "running" && data) {
      setData({ ...data, jobs: data.jobs.map((j) => (j.id === e.id ? { ...j, state: "running", progress: e.progress, message: e.message } : j)) });
    } else reload();
  });

  const jobs = data?.jobs || [];
  const active = jobs.filter((j) => j.state === "queued" || j.state === "running");
  const history = jobs.filter((j) => !(j.state === "queued" || j.state === "running")).sort((a, b) => (b.finished || 0) - (a.finished || 0));

  const drop = async (target: number) => {
    if (dragging === null || dragging === target) return;
    const ids = active.filter((j) => j.state === "queued").map((j) => j.id).filter((id) => id !== dragging);
    ids.splice(ids.indexOf(target), 0, dragging);
    setDragging(null);
    await post("/jobs/reorder", { ids });
    reload();
  };

  const row = (j: Job) => (
    <div key={j.id} className={cx("job", j.id === selected && "sel", j.id === dragging && "dragging")}
      draggable={j.state === "queued"} onDragStart={() => setDragging(j.id)} onDragOver={(e) => e.preventDefault()} onDrop={() => drop(j.id)}
      onClick={() => { setSelected(j.id); setTab("job"); }}>
      <span className="muted">{j.state === "queued" ? "⠿" : ""}</span>
      <span className="muted">#{j.id}</span>
      <span>{j.type}</span>
      <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{describe(j)}</span>
      <span>{j.state === "running" ? <Progress value={j.progress} /> : <span className={j.state === "failed" ? "bad" : j.state === "done" ? "good" : "muted"}>{j.state}</span>}</span>
      <span className="muted">{j.state === "running" ? (eta[j.id] ? `~${fmtDuration(eta[j.id])}` : `${Math.round(j.progress * 100)}%`) : j.finished && j.started ? fmtDuration(j.finished - j.started) : ""}</span>
      <span>{(j.state === "queued" || j.state === "running") && <button className="small danger" onClick={(e) => { e.stopPropagation(); post(`/jobs/${j.id}/cancel`).then(reload); }}>Cancel</button>}</span>
    </div>
  );

  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <h1 style={{ margin: 0 }}>Queue</h1>
        <span className="spacer" />
        {data?.paused ? <button className="primary" onClick={() => post("/queue/resume").then(reload)}>Resume</button>
          : <button onClick={() => post("/queue/pause").then(reload)} title="Finish the current job, then hold">Pause</button>}
        <select value={data?.mode || "focused"} onChange={(e) => post("/queue/mode", { mode: e.target.value }).then(reload)}>
          <option value="focused">Focused (about 80% of the machine)</option>
          <option value="restricted">Restricted (4 GB VRAM free, rests between jobs)</option>
        </select>
      </div>
      <ErrorBox error={error} />
      <div className="card" style={{ padding: 0 }}>
        {active.length ? active.map(row) : <div className="muted" style={{ padding: 10 }}>Nothing queued. {data?.paused && "The queue is paused."}</div>}
      </div>
      <div className="tabs" style={{ marginTop: 12 }}>
        {(["job", "comfy", "engine", "chart"] as const).map((t) => (
          <button key={t} className={cx(tab === t && "active")} onClick={() => setTab(t)}>
            {{ job: selected ? `Job #${selected} log` : "Job log", comfy: "Server (ComfyUI)", engine: "Engine", chart: "Time per track" }[t]}
          </button>
        ))}
      </div>
      {tab === "job" && (selected ? <JobLog id={selected} /> : <div className="muted">Select a job to see its log.</div>)}
      {tab === "comfy" && <SourceLog source="comfy" />}
      {tab === "engine" && <SourceLog source="engine" />}
      {tab === "chart" && <TrackChart />}
      <h2>History</h2>
      <div className="card" style={{ padding: 0, maxHeight: 360, overflow: "auto" }}>{history.map(row)}</div>
    </div>
  );
}

function useTail(ref: React.RefObject<HTMLDivElement>, deps: unknown[]) {
  useEffect(() => {
    const el = ref.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight;
  }, deps);
}

function JobLog({ id }: { id: number }) {
  const [job, setJob] = useState<Job | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    get<Job>(`/jobs/${id}`).then((j) => { setJob(j); setLines((j.log || "").split("\n")); });
  }, [id]);
  useEvents(["log", "job"], (e) => {
    if (e.type === "log" && e.job_id === id) setLines((l) => [...l.slice(-5000), e.line]);
    if (e.type === "job" && e.id === id && e.state !== "running") get<Job>(`/jobs/${id}`).then(setJob);
  });
  useTail(ref, [lines]);
  return (
    <div>
      {job?.error && <div className="error">{job.error}</div>}
      {job?.result && <div className="muted mono" style={{ marginBottom: 4 }}>{JSON.stringify(job.result).slice(0, 400)}</div>}
      <div className="log" ref={ref}>{lines.join("\n")}</div>
    </div>
  );
}

function SourceLog({ source }: { source: "comfy" | "engine" }) {
  const [lines, setLines] = useState<string[]>([]);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { get(`/logs?source=${source}`).then((r) => setLines(r.lines)); }, [source]);
  useEvents(["log"], (e) => { if (e.source === source) setLines((l) => [...l.slice(-5000), e.line]); });
  useTail(ref, [lines]);
  return <div className="log" ref={ref}>{lines.join("\n") || (source === "comfy" ? "No output from ComfyUI yet. A ComfyUI the engine did not start shows its output in its own window." : "")}</div>;
}

// Seconds per generated track over time: the long-run slowdown (2–4 minutes a track drifting to 8–24) shows as a rise.
function TrackChart() {
  const { data } = useLoad(() => get<any[]>("/stats/tracks"));
  const [kind, setKind] = useState("music");
  const points = (data || []).filter((t) => (kind === "music" ? t.kind === "music" : t.kind !== "music"));
  if (!points.length) return <div className="muted">No {kind} tracks generated yet.</div>;
  const w = 900, h = 220, pad = 34;
  const max = Math.max(...points.map((p) => p.seconds)) * 1.1;
  const x = (i: number) => pad + (i / Math.max(points.length - 1, 1)) * (w - pad - 10);
  const y = (s: number) => h - pad - (s / max) * (h - pad - 10);
  return (
    <div className="card">
      <div className="row"><select value={kind} onChange={(e) => setKind(e.target.value)}><option value="music">Music</option><option value="sfx">Effects and ambience</option></select>
        <span className="muted">{points.length} tracks, median {fmtDuration([...points].sort((a, b) => a.seconds - b.seconds)[points.length >> 1].seconds)}</span></div>
      <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ height: h }}>
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}><line x1={pad} x2={w} y1={y(max * f)} y2={y(max * f)} stroke="#313949" /><text x={2} y={y(max * f) + 4} fill="#8d96a3" fontSize={10}>{fmtDuration(max * f)}</text></g>
        ))}
        <polyline fill="none" stroke="#6fc3ff" strokeWidth={1.5} points={points.map((p, i) => `${x(i)},${y(p.seconds)}`).join(" ")} />
        {points.map((p, i) => <circle key={i} cx={x(i)} cy={y(p.seconds)} r={2.5} fill="#f0b450"><title>{`${p.cue}: ${fmtDuration(p.seconds)}`}</title></circle>)}
      </svg>
    </div>
  );
}
