import { useEffect, useRef, useState } from "react";
import { get, post } from "../api";
import { nav } from "../App";
import { useEvents } from "../events";
import { cx } from "../util";

interface AgentEvent { session: string; kind: string; [k: string]: any }

// One line for a tool call, e.g. "Queued 3 takes of mus_frozen_night".
function toolLine(e: AgentEvent) {
  const i = e.input || {};
  switch (e.tool) {
    case "queue_generate": return `Queue takes of ${(i.cues || []).join(", ")}${i.takes ? ` (up to ${i.takes})` : ""}`;
    case "edit_cue": return `Edit ${i.id}: ${Object.keys(i.fields || {}).join(", ")}`;
    case "get_cue": return `Read ${i.id}`;
    case "map_track": return `Map ${i.file}`;
    case "cut_loop": return `Cut a ${i.min ?? 30}–${i.max ?? 45} s loop from ${i.take}`;
    case "recompose": return `Recompose ${i.take} from ${(i.pieces || []).length} pieces`;
    case "promote": return `Install ${(i.candidates || []).join(", ")}`;
    default: return `${e.tool} ${Object.keys(i).length ? JSON.stringify(i).slice(0, 80) : ""}`;
  }
}

export default function AgentPanel({ enabled }: { enabled: boolean }) {
  const [open, setOpen] = useState(() => localStorage.getItem("agentOpen") !== "no");
  const [sessions, setSessions] = useState<any[]>([]);
  const [session, setSession] = useState<string | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [usage, setUsage] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => { localStorage.setItem("agentOpen", open ? "yes" : "no"); }, [open]);
  const loadSessions = () => get<any[]>("/agent/sessions").then((s) => { setSessions(s); if (!session && s[0]) setSession(s[0].id); }).catch(() => {});
  useEffect(() => { if (enabled) loadSessions(); }, [enabled]);
  useEffect(() => {
    if (!session) return setEvents([]);
    get(`/agent/sessions/${session}`).then((r) => { setEvents(r.events); setBusy(r.busy); setUsage(r.meta?.usage); });
  }, [session]);
  useEvents(["agent"], (e: AgentEvent) => {
    // An outside agent (MCP) asking for approval: show its conversation so the request can be answered.
    if (e.session === "external" && e.kind === "permission" && session !== "external") { setOpen(true); setSession("external"); loadSessions(); return; }
    if (e.session !== session) return;
    setEvents((list) => [...list, e]);
    if (e.kind === "done") { setBusy(false); if (e.usage) setUsage(e.usage); }
  });
  useEffect(() => { list.current?.scrollTo(0, list.current.scrollHeight); }, [events]);

  const send = async () => {
    if (!text.trim()) return;
    setErr(null);
    let id = session;
    if (!id) { id = (await post("/agent/sessions")).id; setSession(id); loadSessions(); }
    try { await post(`/agent/sessions/${id}/messages`, { text }); setText(""); setBusy(true); } catch (e: any) { setErr(e.message); }
  };
  const answer = (request_id: string, allow: boolean, always = false) => post(`/agent/sessions/${session}/approve`, { request_id, allow, always });

  if (!open) return <aside className="agent collapsed"><button className="icon" style={{ margin: 4 }} title="Agent" onClick={() => setOpen(true)}>◀</button></aside>;
  const answered = new Set(events.filter((e) => e.kind === "permission_answer").map((e) => e.request_id));
  const results = new Map(events.filter((e) => e.kind === "tool_result").map((e) => [e.id, e]));
  return (
    <aside className="agent">
      <div className="row" style={{ padding: 8, borderBottom: "1px solid var(--line)" }}>
        <b>Agent</b>
        {enabled && <select className="grow" value={session || ""} onChange={(e) => setSession(e.target.value || null)}>
          <option value="">New conversation</option>
          {sessions.map((s) => <option key={s.id} value={s.id}>{s.title || s.id}</option>)}
        </select>}
        <button className="icon" title="Hide" onClick={() => setOpen(false)}>▶</button>
      </div>
      {!enabled ? (
        <div className="messages muted">The agent needs an open project and an agent set up on the <a href="#" onClick={() => nav.go("Setup")}>Setup</a> screen: Claude, any OpenAI-compatible API, or an outside agent over MCP. Everything else works without it.</div>
      ) : (
        <>
          <div className="messages" ref={list}>
            {!events.length && <div className="muted">Try: "the frozen night track is too busy, make it sparse and give me two new takes", or "which cues still have no sound?"</div>}
            {events.map((e, i) => {
              if (e.kind === "user") return <div key={i} className="msg user">{e.text}</div>;
              if (e.kind === "text") return <div key={i} className="msg text">{e.text}</div>;
              if (e.kind === "error") return <div key={i} className="msg err">{e.text}</div>;
              if (e.kind === "tool_use") {
                const result = results.get(e.id);
                let parsed: any = null;
                try { parsed = result ? JSON.parse(result.text) : null; } catch { /* plain text result */ }
                const jobs: number[] = parsed?.queued || [];
                return (
                  <div key={i} className={cx("msg tool", result?.error && "err")}>
                    {toolLine(e)} {!result && <span className="muted">…</span>}
                    {parsed?.error && <div className="bad">{parsed.error}</div>}
                    {jobs.map((j) => <a key={j} href="#" onClick={() => nav.go("Queue", { job: j })} style={{ marginLeft: 6 }}>job #{j}</a>)}
                    {parsed?.installed?.map((x: any) => <div key={x.path} className="good">installed {x.path}</div>)}
                    {parsed?.estimated_minutes && <span className="muted"> about {parsed.estimated_minutes} min</span>}
                  </div>
                );
              }
              if (e.kind === "permission") {
                const done = answered.has(e.request_id);
                return (
                  <div key={i} className="msg perm">
                    <div>Allow <b>{e.tool}</b>? <span className="muted">({e.reason})</span></div>
                    <div className="mono muted" style={{ fontSize: 11 }}>{JSON.stringify(e.input).slice(0, 300)}</div>
                    {!done && <div className="row" style={{ marginTop: 4 }}>
                      <button className="small good" onClick={() => answer(e.request_id, true)}>Allow</button>
                      {e.can_always && <button className="small" onClick={() => answer(e.request_id, true, true)}>Allow always for this tool</button>}
                      <button className="small danger" onClick={() => answer(e.request_id, false)}>Deny</button>
                    </div>}
                  </div>
                );
              }
              if (e.kind === "permission_answer") return <div key={i} className="muted" style={{ fontSize: 11 }}>{e.allow ? (e.always ? "allowed always" : "allowed") : "denied"}</div>;
              return null;
            })}
            {busy && <div className="muted">working…</div>}
          </div>
          {err && <div className="error" style={{ margin: 8 }}>{err}</div>}
          <div style={{ padding: 8, borderTop: "1px solid var(--line)" }} className="col">
            <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask the agent…"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} style={{ fontFamily: "inherit", fontSize: 13 }} />
            <div className="row">
              <span className="muted" style={{ fontSize: 11 }}>{usage ? `${(usage.input_tokens / 1000).toFixed(1)}k in · ${(usage.output_tokens / 1000).toFixed(1)}k out · $${usage.cost_usd}` : ""}</span>
              <span className="spacer" />
              {busy && <button className="danger" onClick={() => post(`/agent/sessions/${session}/stop`)}>Stop</button>}
              <button onClick={() => { setSession(null); setEvents([]); setUsage(null); }}>New</button>
              <button className="primary" disabled={busy || !text.trim()} onClick={send}>Send</button>
            </div>
          </div>
        </>
      )}
    </aside>
  );
}
