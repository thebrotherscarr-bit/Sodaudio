// The one module that talks to the engine (ENGINE-API.md). Everything is under /api on the engine's own origin, in a
// browser tab, an installed PWA window and Electron alike.
export const API = "/api";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(API + path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) throw new ApiError(response.status, (data && data.detail) || response.statusText);
  return data as T;
}

export const get = <T = any>(path: string) => call<T>("GET", path);
export const post = <T = any>(path: string, body: unknown = {}) => call<T>("POST", path, body);
export const put = <T = any>(path: string, body: unknown) => call<T>("PUT", path, body);
export const patch = <T = any>(path: string, body: unknown) => call<T>("PATCH", path, body);
export const qs = (params: Record<string, string | number | boolean | undefined>) =>
  "?" + Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

// Where the browser can fetch audio from.
export const workspaceUrl = (path: string) => `${API}/files/${path.split("/").map(encodeURIComponent).join("/")}`;
export const gameUrl = (path: string) => `${API}/game/${path.split("/").map(encodeURIComponent).join("/")}`;
export const fileUrl = (ident: string) =>
  ident.startsWith("game:") ? gameUrl(ident.slice(5)) : /^[a-zA-Z]:[\\/]|^\//.test(ident) ? `${API}/abs${qs({ path: ident })}` : workspaceUrl(ident);

export async function upload(file: File): Promise<{ file: string }> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(API + "/upload", { method: "POST", body: form });
  if (!response.ok) throw new ApiError(response.status, (await response.json()).detail);
  return response.json();
}

export interface Health {
  ok: boolean; version: string; comfy: "up" | "down" | "starting"; gpu: { name: string; vram_free_gb: number; vram_total_gb: number } | null;
  busy: boolean; paused: boolean; mode: "focused" | "restricted"; generator: string; queued: number; project: string | null; agent: boolean;
  running: { id: number; type: string; progress: number; message?: string } | null;
}

export interface Cue {
  id: string; category: string; kind: "music" | "ambience" | "sfx"; trigger: string; variants: number; seconds: number; loop: boolean;
  offset_db: number; bpm: number | null; key: string; meter: number | null; card: string; voice: string; context: string;
  plan: string | null; prompt: string | null; locked: boolean; notes: string; installed: string[]; takes: number; candidates: number;
  missing?: boolean; prompt_final?: string; plan_final?: string | null; shape?: string | null;
}

export interface Take {
  name: string; kind: "raw" | "candidate"; path: string; take: number | null; seconds: number | null; lufs: number | null;
  tempo: number | null; installed: string | null; rejected: boolean; modified: number;
}

export interface Job {
  id: number; type: string; body: any; state: "queued" | "running" | "done" | "failed" | "cancelled"; progress: number;
  message: string | null; created: number; started: number | null; finished: number | null; result: any; error: string | null; log?: string;
}

export interface MapData {
  file: string; name?: string; duration: number; tempo: number; asked_bpm: number | null; meter: number; beats: number[]; bars: number[];
  hop_s: number; key: string; loudness: number[]; low: number[]; drums: number[]; mel: { bins: number; frames: number[][] }; chroma: number[][];
}

// Wait for a job to finish (through the event stream's job events, polling as a fallback).
export async function waitForJob(id: number, timeoutMs = 600000): Promise<Job> {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const job = await get<Job>(`/jobs/${id}`);
    if (["done", "failed", "cancelled"].includes(job.state)) return job;
    await new Promise((r) => setTimeout(r, 700));
  }
  throw new Error("timed out waiting for job " + id);
}
