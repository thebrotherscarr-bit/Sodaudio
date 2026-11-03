import { useState } from "react";
import { get, post, put, waitForJob } from "../api";
import { nav } from "../App";
import { ErrorBox, FolderBrowser } from "../components/common";
import { native } from "../native";
import { useLoad } from "../util";

const PROVIDERS: [string, string][] = [
  ["OpenAI", "https://api.openai.com/v1"],
  ["Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"],
  ["Mistral", "https://api.mistral.ai/v1"],
  ["Groq", "https://api.groq.com/openai/v1"],
  ["OpenRouter (many models)", "https://openrouter.ai/api/v1"],
  ["Ollama on this PC", "http://127.0.0.1:11434/v1"],
  ["LM Studio on this PC", "http://127.0.0.1:1234/v1"],
];

const POINT_FIELDS: [string, string, "folder" | "file"][] = [
  ["comfy_path", "ComfyUI install (the folder holding ComfyUI/main.py)", "folder"],
  ["comfy_python", "ComfyUI's Python (optional: found from the install)", "file"],
  ["comfy_extra_model_paths", "Extra model paths yaml (Comfy Desktop)", "file"],
  ["comfy_extra_args", "Extra ComfyUI arguments", "file"],
  ["comfy_start_script", "Start script instead (e.g. a game's start_comfy.ps1)", "file"],
  ["moss_python", "MOSS venv python.exe", "file"],
  ["moss_model_dir", "MOSS-SoundEffect-v2.0 model folder", "folder"],
];

export default function Setup() {
  const check = useLoad(() => get("/setup/check"));
  const settings = useLoad(() => get("/settings"));
  const mcp = useLoad(() => get("/agent/mcp")).data;
  const [form, setForm] = useState<Record<string, any>>({});
  const [key, setKey] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [browse, setBrowse] = useState<null | { field: string; mode: "folder" | "file" }>(null);
  const s = { ...(settings.data || {}), ...form };

  const save = async (changes: Record<string, any>) => {
    try { await put("/settings", changes); settings.reload(); check.reload(); setForm({}); setMsg("Saved."); } catch (e: any) { setErr(e.message); }
  };
  // Electron keeps the key with safeStorage and hands it to the engine; the browser build uses the engine's keyring.
  const saveKey = async (value: string) => {
    const n = native() as any;
    try {
      if (n?.setApiKey) { setMsg("Restarting the engine with the key…"); await n.setApiKey(value); setMsg("Saved."); settings.reload(); check.reload(); }
      else await save({ api_key: value });
    } catch (e: any) { setErr(e.message); }
  };
  const point = async () => {
    try { await post("/setup/point", form); setForm({}); settings.reload(); check.reload(); setMsg("Saved."); } catch (e: any) { setErr(e.message); }
  };
  const run = async (steps?: string[]) => {
    const what = steps ? steps.join(", ") : "find, download models, build the MOSS venv and run the smoke test";
    if (!confirm(`Run setup (${what})? Model downloads are about 14 GB and the smoke test uses the GPU.`)) return;
    const r = await post("/setup/run", { steps });
    setMsg(`Setup job #${r.ids[0]} queued; follow it on the Queue screen.`);
    waitForJob(r.ids[0], 24 * 3600 * 1000).then(() => { check.reload(); settings.reload(); });
  };
  const pick = async (field: string, mode: "folder" | "file") => {
    const n = native();
    if (n) {
      const path = mode === "folder" ? await n.pickFolder() : await n.pickFile();
      if (path) setForm((f) => ({ ...f, [field]: path }));
    } else setBrowse({ field, mode });
  };
  const autostart = async (enabled: boolean) => {
    if (native() && (window as any).native.setStartWithWindows) await (window as any).native.setStartWithWindows(enabled);
    else await post("/autostart", { enabled });
    settings.reload();
  };

  return (
    <div style={{ maxWidth: 980 }}>
      <h1>Setup</h1>
      <ErrorBox error={check.error || settings.error || err} />
      {msg && <div className="card" style={{ marginBottom: 8 }}>{msg}</div>}
      <div className="card">
        <div className="row"><b>Checks</b>{check.data && (check.data.ready ? <span className="pill good">ready</span> : <span className="pill bad">parts missing</span>)}
          <span className="spacer" /><button onClick={check.reload}>Check again</button>
          <button className="primary" onClick={() => run()}>Install missing…</button></div>
        <table className="grid" style={{ marginTop: 6 }}>
          <tbody>
            {check.data?.parts.map((p: any) => (
              <tr key={p.part}><td>{p.ok ? <span className="good">✔</span> : <span className={p.optional ? "warm" : "bad"}>✘</span>}</td><td>{p.part}</td>
                <td className="mono muted">{p.detail}{p.found?.length ? ` (found: ${p.found.join(", ")})` : ""}</td><td className="muted">{!p.ok && p.fix}</td></tr>
            ))}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 6 }}>
          <span className="muted">Steps on their own:</span>
          {["find", "models", "moss", "smoke"].map((step) => <button key={step} className="small" onClick={() => run([step])}>{step}</button>)}
          <button className="small" onClick={() => nav.go("Queue")}>Open the queue</button>
        </div>
      </div>

      <h2>Point at existing</h2>
      <div className="card col">
        <div className="row">
          <span className="muted grow">A ComfyUI already running at the address below (Comfy Desktop, or one a game's tools started) is used as it is: never started, stopped or restarted.
            To start it headless the way a game's overnight runs did, use that game's own setup:</span>
          <button onClick={async () => {
            const r = await post("/setup/adopt", { apply: true });
            setMsg(r.root ? `Using ${r.root}'s own tools: ${Object.keys(r.found).join(", ")}.` : "No game folder with tools/comfy-app found among your projects.");
            settings.reload(); check.reload();
          }}>Use the game's own ComfyUI and MOSS</button>
        </div>
        {POINT_FIELDS.map(([field, label, mode]) => (
          <div key={field} className="row">
            <label style={{ width: 300 }}>{label}</label>
            <input className="grow mono" value={s[field] || ""} onChange={(e) => setForm({ ...form, [field]: e.target.value })} />
            {field !== "comfy_extra_args" && <button className="small" onClick={() => pick(field, mode)}>Pick…</button>}
          </div>
        ))}
        <div className="row"><label style={{ width: 300 }}>ComfyUI address</label><input className="grow mono" value={s.comfy_url || ""} onChange={(e) => setForm({ ...form, comfy_url: e.target.value })} /></div>
        <div className="row"><span className="muted">If something already answers at that address, the engine uses it and never starts or stops it.</span><span className="spacer" />
          <button className="primary" disabled={!Object.keys(form).length} onClick={point}>Save paths</button></div>
      </div>

      <h2>Running</h2>
      <div className="card col">
        <div className="row"><label style={{ width: 300 }}>GPU mode</label>
          <select value={s.gpu_mode} onChange={(e) => save({ gpu_mode: e.target.value })}>
            <option value="focused">Focused: about 80% of the machine (1.5 GB VRAM left free)</option>
            <option value="restricted">Restricted: 4 GB VRAM free, below-normal priority, rests between jobs</option>
          </select></div>
        <div className="row"><label style={{ width: 300 }}>Restart a ComfyUI we started after</label>
          <input type="number" value={s.restart_every ?? 10} onChange={(e) => setForm({ ...form, restart_every: Number(e.target.value) })} style={{ width: 70 }} /> tracks
          <label>rest between jobs (restricted)</label><input type="number" value={s.rest_seconds ?? 20} onChange={(e) => setForm({ ...form, rest_seconds: Number(e.target.value) })} style={{ width: 70 }} /> s
          <button className="small" onClick={() => save({ restart_every: s.restart_every, rest_seconds: s.rest_seconds })}>Save</button></div>
        <div className="row"><label style={{ width: 300 }}>Generator</label>
          <select value={s.generator} onChange={(e) => save({ generator: e.target.value })}>
            <option value="comfy">ACE-Step (ComfyUI) and MOSS: the real models</option>
            <option value="fake">Fake: test tones, no GPU (for trying the app)</option>
          </select></div>
        <div className="row"><label style={{ width: 300 }}>Notifications when long jobs finish</label>
          <input type="checkbox" checked={!!s.notify} onChange={(e) => save({ notify: e.target.checked })} /></div>
        <div className="row"><label style={{ width: 300 }}>Start with Windows</label>
          <input type="checkbox" checked={!!s.start_with_windows} onChange={(e) => autostart(e.target.checked)} />
          <span className="muted">{native() ? "starts the app minimised to the tray" : "starts the engine (start.cmd --no-open) at sign-in"}</span></div>
        {native() && <>
          <div className="row"><label style={{ width: 300 }}>Keep running in the tray when closed</label>
            <input type="checkbox" checked={!!s.keep_in_tray} onChange={(e) => save({ keep_in_tray: e.target.checked })} /></div>
          <div className="row"><label style={{ width: 300 }}>Keep the PC awake while the queue runs</label>
            <input type="checkbox" checked={!!s.keep_awake} onChange={(e) => save({ keep_awake: e.target.checked })} /></div>
        </>}
      </div>

      <h2>Agent</h2>
      <div className="card col">
        <div className="row"><label style={{ width: 300 }}>Agent in the panel</label>
          <select value={s.agent_provider || "claude"} onChange={(e) => save(e.target.value === "claude"
            ? { agent_provider: "claude", agent_model: "claude-opus-5-5" } : { agent_provider: "openai", agent_model: "", agent_base_url: s.agent_base_url || PROVIDERS[0][1] })}>
            <option value="claude">Claude (Claude Agent SDK)</option>
            <option value="openai">Any OpenAI-compatible API (OpenAI, Gemini, Mistral, OpenRouter, Ollama, LM Studio …)</option>
          </select></div>
        {(s.agent_provider || "claude") === "claude" ? <>
          <div className="row"><label style={{ width: 300 }}>Model</label>
            <select value={s.agent_model} onChange={(e) => save({ agent_model: e.target.value })}>
              <option value="claude-opus-5-5">Claude Opus 5.5 (default)</option>
              <option value="claude-sonnet-5-5">Claude Sonnet 5.5 (cheaper)</option>
            </select></div>
          <div className="row"><label style={{ width: 300 }}>Claude API key {s.api_key_set ? <span className="good">(set)</span> : <span className="muted">(not set)</span>}</label>
            <input className="grow mono" type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="sk-ant-…" />
            <button className="primary" disabled={!key} onClick={() => { saveKey(key); setKey(""); }}>Save key</button>
            {s.api_key_set && <button className="danger" onClick={() => saveKey("")}>Remove</button>}</div>
        </> : <>
          <div className="row"><label style={{ width: 300 }}>Provider</label>
            <select value={PROVIDERS.find(([, url]) => url === s.agent_base_url)?.[1] || ""} onChange={(e) => e.target.value && save({ agent_base_url: e.target.value })}>
              <option value="">Other (type the address)</option>
              {PROVIDERS.map(([name, url]) => <option key={url} value={url}>{name}</option>)}
            </select></div>
          <div className="row"><label style={{ width: 300 }}>API address (…/v1)</label>
            <input className="grow mono" value={form.agent_base_url ?? s.agent_base_url ?? ""} onChange={(e) => setForm({ ...form, agent_base_url: e.target.value })} /></div>
          <div className="row"><label style={{ width: 300 }}>Model</label>
            <input className="grow mono" value={form.agent_model ?? s.agent_model ?? ""} onChange={(e) => setForm({ ...form, agent_model: e.target.value })}
              placeholder="the provider's model name, e.g. qwen3:14b on Ollama" />
            <button className="small" disabled={form.agent_base_url === undefined && form.agent_model === undefined}
              onClick={() => save({ agent_base_url: s.agent_base_url, agent_model: s.agent_model })}>Save</button></div>
          <div className="row"><label style={{ width: 300 }}>API key {s.agent_key_set ? <span className="good">(set)</span> : <span className="muted">(none: fine for local servers)</span>}</label>
            <input className="grow mono" type="password" value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="primary" disabled={!key} onClick={() => { save({ agent_key: key }); setKey(""); }}>Save key</button>
            {s.agent_key_set && <button className="danger" onClick={() => save({ agent_key: "" })}>Remove</button>}</div>
          <div className="muted">The model must support tool calls. A local model shares the GPU with ACE-Step and MOSS: one focus at a time.</div>
        </>}
        <div className="muted">Keys are kept in Windows Credential Manager (in the desktop app, the Claude key is encrypted with Windows' data protection), never in the settings file or the project. Without an agent, everything but the agent panel works.</div>
      </div>

      <h2>Outside agents (MCP)</h2>
      <div className="card col">
        <div className="muted">Any agent that speaks MCP (Codex, Gemini CLI, Claude Code, opencode, Cursor …) can direct the audio through the engine while it runs.
          Its calls show in the agent panel under "Outside agents", and installs and big runs wait for your Allow there.</div>
        {mcp && <>
          <label>Claude Code</label><div className="final">{mcp.claude_code}</div>
          <label>Codex (~/.codex/config.toml)</label><div className="final">{mcp.codex_toml}</div>
          <label>Gemini CLI, opencode, Cursor and other JSON configs (mcpServers)</label><div className="final">{JSON.stringify(mcp.json, null, 2)}</div>
        </>}
      </div>
      {browse && <FolderBrowser mode={browse.mode} onClose={() => setBrowse(null)} onPick={(p) => { setForm((f) => ({ ...f, [browse.field]: p })); setBrowse(null); }} />}
    </div>
  );
}
