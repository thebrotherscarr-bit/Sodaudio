import { useEffect, useMemo, useRef, useState } from "react";
import { Cue, get, gameUrl, patch, post, qs, Take, workspaceUrl } from "../api";
import { nav } from "../App";
import { ErrorBox, PlayButton } from "../components/common";
import { useEvents } from "../events";
import { cx, fmtTime, useLoad } from "../util";

const STATUSES = [["", "All"], ["no-sound", "No sound"], ["has-candidates", "Has candidates"], ["locked", "Locked"]];

export default function Cues({ initial }: { initial?: string }) {
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(initial || null);
  const [msg, setMsg] = useState<string | null>(null);
  const all = useLoad(() => get<Cue[]>("/cues"));
  const { data, error, reload } = useLoad(() => get<Cue[]>("/cues" + qs({ category, q, status })), [category, q, status]);
  useEvents(["cues", "take", "install"], () => { reload(); });
  const categories = useMemo(() => [...new Set((all.data || []).map((c) => c.category))].sort(), [all.data]);

  const tool = async (what: string) => {
    try {
      if (what === "import") {
        const r = await post("/project/import", { run_command: true });
        setMsg(`Imported: ${r.added.length} added, ${r.changed.length} changed, ${r.missing.length} missing.`);
      } else if (what === "decompose") {
        const r = await post("/project/decompose");
        setMsg(`Split ${r.changed.length} prompts into card, voice and context.`);
      } else if (what === "undo") {
        const r = await post("/project/undo");
        setMsg(r.undone ? `Undid a change to ${r.undone.cue || "the project"}.` : "Nothing to undo.");
      } else if (what === "add") {
        const id = prompt("New cue id (for example ui_click_soft)");
        if (!id) return;
        const category = prompt("Category (sfx group such as ui, weapons; music; ambience)", "ui") || "ui";
        await post("/cues", { id, category });
        setSelected(id);
      } else if (what === "fill") {
        if (!confirm("Install first takes for every cue that has no file?")) return;
        const r = await post("/promote/fill-gaps");
        setMsg(`Installed ${r.installed.length} files.`);
      }
      reload(); all.reload();
    } catch (e: any) { setMsg(e.message); }
  };

  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}>
        <h1 style={{ margin: 0 }}>Cues</h1>
        <span className="muted">{data?.length ?? "…"} shown</span>
        <span className="spacer" />
        <button onClick={() => tool("import")}>Import from game</button>
        <button onClick={() => tool("decompose")} title="Split imported prompts into card, voice and context so the style can be edited here">Decompose prompts</button>
        <button onClick={() => tool("undo")}>Undo</button>
        <button onClick={() => tool("add")}>Add cue</button>
        <button onClick={() => tool("fill")}>Fill gaps</button>
      </div>
      <ErrorBox error={error} />
      {msg && <div className="card" style={{ marginBottom: 8 }}>{msg}</div>}
      <div className="split">
        <div>
          <div className="row" style={{ marginBottom: 6 }}>
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All categories</option>
              {categories.map((c) => <option key={c}>{c}</option>)}
            </select>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <input className="grow" placeholder="Search id or trigger" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <table className="grid">
            <thead><tr><th>Cue</th><th>Category</th><th>Trigger</th><th>Game</th><th>Takes</th><th>Cand.</th></tr></thead>
            <tbody>
              {data?.map((c) => (
                <tr key={c.id} className={cx(c.id === selected && "sel")} onClick={() => setSelected(c.id)}>
                  <td className="mono">{c.id} {c.locked && <span className="pill warm">locked</span>}</td>
                  <td>{c.category}</td>
                  <td className="muted">{c.trigger}</td>
                  <td onClick={(e) => e.stopPropagation()}>{c.installed.length ? <><PlayButton url={gameUrl(c.installed[0])} /> {c.installed.length}</> : <span className="bad">none</span>}</td>
                  <td>{c.takes}</td>
                  <td>{c.candidates}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="sticky">{selected ? <CueEditor key={selected} id={selected} onChanged={reload} /> : <div className="muted card">Select a cue to edit it.</div>}</div>
      </div>
    </div>
  );
}

const FIELDS: [keyof Cue, string, string][] = [
  ["seconds", "Length (s)", "number"], ["loop", "Loop", "bool"], ["offset_db", "Level offset (dB)", "number"], ["variants", "Variants", "number"],
  ["bpm", "BPM (asked)", "number"], ["key", "Key", "text"], ["meter", "Meter", "number"], ["locked", "Locked", "bool"],
];

function CueEditor({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { data: cue, error, reload } = useLoad(() => get<Cue>(`/cues/${id}`), [id]);
  const takes = useLoad(() => get<{ raw: Take[]; candidates: Take[]; installed: any[] }>(`/cues/${id}/takes`), [id]);
  const [edit, setEdit] = useState<Partial<Cue>>({});
  const [preview, setPreview] = useState<{ prompt_final: string; plan_final: string | null; shape?: string } | null>(null);
  const [n, setN] = useState(2);
  const [err, setErr] = useState<string | null>(null);
  const [queued, setQueued] = useState<string | null>(null);
  const timer = useRef<any>();
  useEvents(["take", "install"], (e) => { if (e.cue === id) { takes.reload(); reload(); } });

  const merged = { ...(cue || {}), ...edit } as Cue;
  const dirty = Object.keys(edit).length > 0;
  useEffect(() => {
    clearTimeout(timer.current);
    if (!cue) return;
    timer.current = setTimeout(() => post("/preview", { cue: { ...edit, id } }).then(setPreview).catch(() => {}), 250);
  }, [edit, cue]);

  const set = (k: keyof Cue, v: any) => setEdit((e) => ({ ...e, [k]: v }));
  const save = async () => {
    try { await patch(`/cues/${id}`, edit); setEdit({}); reload(); onChanged(); } catch (e: any) { setErr(e.message); }
  };
  const generate = async () => {
    try {
      if (dirty) await save();
      const r = await post("/jobs", { type: "generate", cues: [id], takes: (cue?.takes || 0) + n });
      setQueued(r.takes.length ? `Queued ${r.takes.join(", ")}.` : "Nothing to make: those takes exist.");
    } catch (e: any) { setErr(e.message); }
  };
  const variations = async (name: string) => {
    const semis = prompt("Semitones to pitch by (comma-separated)", "-5,5");
    if (semis === null) return;
    await post("/jobs", { type: "variations", candidate: name, kinds: ["reverse", "pitch"], semitones: semis.split(",").map((s) => parseInt(s.trim())).filter((x) => !isNaN(x)) });
    setQueued("Variations queued; they appear as candidates.");
  };

  if (error) return <ErrorBox error={error} />;
  if (!cue) return <div className="muted">Loading…</div>;
  const music = cue.kind === "music";
  return (
    <div className="card col">
      <div className="row">
        <h2 style={{ margin: 0 }} className="mono">{cue.id}</h2>
        <span className="pill">{cue.category}</span>
        {preview?.shape && <span className="pill">{preview.shape}</span>}
        <span className="spacer" />
        <button disabled={!dirty} onClick={() => setEdit({})}>Revert</button>
        <button className="primary" disabled={!dirty} onClick={save}>Save</button>
      </div>
      <ErrorBox error={err} />
      <div className="fields">
        <div className="wide"><label>Trigger</label><input value={merged.trigger || ""} onChange={(e) => set("trigger", e.target.value)} /></div>
        <div className="wide"><label>Card (what this cue is, in a line)</label><input value={merged.card || ""} onChange={(e) => set("card", e.target.value)} /></div>
        {music && <div className="half"><label>Voice (how the choir sings here)</label><input value={merged.voice || ""} onChange={(e) => set("voice", e.target.value)} /></div>}
        {music && <div className="half"><label>Context</label><input value={merged.context || ""} onChange={(e) => set("context", e.target.value)} /></div>}
        {FIELDS.filter(([k]) => music || !["bpm", "key", "meter"].includes(k)).map(([k, label, type]) => (
          <div key={k}>
            <label>{label}</label>
            {type === "bool"
              ? <select value={merged[k] ? "yes" : "no"} onChange={(e) => set(k, e.target.value === "yes")}><option>yes</option><option>no</option></select>
              : <input type={type} value={(merged[k] as any) ?? ""} onChange={(e) => set(k, type === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)} />}
          </div>
        ))}
        <div className="wide"><label>Prompt override (empty: built from the template)</label>
          <textarea rows={2} value={merged.prompt || ""} onChange={(e) => set("prompt", e.target.value || null)} /></div>
        {music && <div className="wide"><label>Song plan (empty: built from the chant; "[Instrumental]" for none)</label>
          <textarea rows={3} value={merged.plan || ""} onChange={(e) => set("plan", e.target.value || null)} /></div>}
        <div className="wide"><label>Notes (what you liked, what to avoid; the agent reads them)</label>
          <textarea rows={2} value={merged.notes || ""} onChange={(e) => set("notes", e.target.value)} /></div>
      </div>
      <label>Final prompt</label>
      <div className="final">{preview?.prompt_final ?? cue.prompt_final}</div>
      {music && <><label>Final song plan</label><div className="final">{preview?.plan_final ?? cue.plan_final}</div></>}
      <div className="row">
        <button className="primary" onClick={generate} disabled={cue.locked}>Generate</button>
        <input type="number" min={1} max={20} value={n} onChange={(e) => setN(Number(e.target.value))} style={{ width: 56 }} />
        <span className="muted">more takes ({cue.takes} now)</span>
        {queued && <span className="good">{queued}</span>}
      </div>
      <h3>In the game</h3>
      {takes.data?.installed.length ? takes.data.installed.map((f) => (
        <div key={f.path} className="row"><PlayButton url={gameUrl(f.path)} /><span className="mono">{f.path}</span>
          <span className="muted">{fmtTime(f.seconds)}</span>{f.generated ? <span className="pill">installed by the tool</span> : <span className="pill good">your file</span>}</div>
      )) : <div className="muted">Nothing yet: the game falls back to silence or a placeholder.</div>}
      <h3>Candidates</h3>
      {takes.data?.candidates.map((t) => (
        <div key={t.name} className="row">
          <PlayButton url={workspaceUrl(t.path)} />
          <span className="mono grow">{t.name}</span>
          <span className="muted">{fmtTime(t.seconds)}</span>
          {t.lufs !== null && <span className="muted">{t.lufs} LUFS</span>}
          {t.installed && <span className="pill good">installed</span>}
          {t.rejected && <span className="pill bad">rejected</span>}
          <button className="small" onClick={() => variations(t.name)}>Variations</button>
          {music && <button className="small" onClick={() => nav.go("Map", { file: t.path })}>Map</button>}
        </div>
      ))}
      <h3>Raw takes</h3>
      {takes.data?.raw.map((t) => (
        <div key={t.name} className="row">
          <PlayButton url={workspaceUrl(t.path)} /><span className="mono grow">{t.name}</span><span className="muted">{fmtTime(t.seconds)}</span>
          {t.tempo && <span className="muted">{t.tempo.toFixed(1)} BPM measured</span>}
          {music && <button className="small" onClick={() => nav.go("Map", { file: t.path })}>Map</button>}
        </div>
      ))}
    </div>
  );
}
