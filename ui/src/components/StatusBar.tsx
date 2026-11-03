import { useEffect, useRef, useState } from "react";
import { Health, post } from "../api";
import { useEvents } from "../events";
import { notify, setTaskbarProgress } from "../native";
import { Eta } from "./common";

export default function StatusBar({ health, connected }: { health: Health | null; connected: boolean }) {
  const [job, setJob] = useState<{ id: number; progress: number; eta_s?: number; message?: string; job_type?: string } | null>(null);
  const started = useRef<Record<number, number>>({});
  const longRun = useRef(false);

  useEvents(["job", "queue"], (e) => {
    if (e.type === "job") {
      if (e.state === "running") {
        started.current[e.id] ??= Date.now();
        setJob(e);
        setTaskbarProgress(e.progress);
      } else if (["done", "failed", "cancelled"].includes(e.state)) {
        const took = (Date.now() - (started.current[e.id] || Date.now())) / 1000;
        if (took > 120) {
          longRun.current = true;
          notify(`Job ${e.id} ${e.state}`, `${e.job_type || "job"} took ${Math.round(took / 60)} min`, e.id);
        }
        setJob((j) => (j && j.id === e.id ? null : j));
      }
    } else if (e.type === "queue" && e.idle) {
      setTaskbarProgress(-1);
      if (longRun.current) notify("Queue finished", "Every queued job is done. Listen in Audition.");
      longRun.current = false;
    }
  });
  useEffect(() => { if (!health?.busy) setJob(null); }, [health?.busy]);

  const running = job || (health?.running ? { id: health.running.id, progress: health.running.progress, message: health.running.message, job_type: health.running.type } : null);
  return (
    <footer className="status">
      <span title="ComfyUI"><span className={`dot ${connected ? health?.comfy : "down"}`} />ComfyUI {health?.comfy ?? "?"}</span>
      {health?.gpu && <span title={health.gpu.name}>GPU {health.gpu.vram_free_gb.toFixed(1)} / {health.gpu.vram_total_gb.toFixed(1)} GB free</span>}
      {health?.generator === "fake" && <span className="warm">fake generator</span>}
      <span className="spacer" />
      {running ? (
        <span className="row">
          <span>#{running.id} {running.job_type} {running.message ? `– ${running.message}` : ""}</span>
          <span className="bar"><div style={{ width: `${Math.round(running.progress * 100)}%` }} /></span>
          <Eta seconds={(job as any)?.eta_s} />
        </span>
      ) : <span className="muted">{health?.queued ? `${health.queued} queued` : "idle"}</span>}
      {health?.paused && <span className="warm">paused</span>}
      <button className="small" title="GPU power: focused uses about 80% of the machine; restricted leaves 4 GB of VRAM free"
        onClick={() => post("/queue/mode", { mode: health?.mode === "focused" ? "restricted" : "focused" })}>
        {health?.mode ?? "focused"}
      </button>
      {!connected && <span className="bad">engine disconnected</span>}
    </footer>
  );
}
