import { useEffect, useMemo, useState } from "react";
import { gameUrl, get, post, qs, Take, workspaceUrl } from "../api";
import { nav } from "../App";
import { ErrorBox, PlayButton, Waveform } from "../components/common";
import { useEvents } from "../events";
import { reveal } from "../native";
import { deck, useDeck } from "../player";
import { cx, fmtTime, useLoad } from "../util";

interface Group { cue: { id: string; category: string; kind: string; trigger: string; loop: boolean; locked: boolean; variants: number; notes: string };
  installed: { path: string; generated: boolean; seconds: number }[]; candidates: Take[] }

export default function Audition() {
  const [category, setCategory] = useState("");
  const [musicOnly, setMusicOnly] = useState(false);
  const [newOnly, setNewOnly] = useState(false);
  const [since, setSince] = useState(0);
  const { data, error, reload } = useLoad(() => get<{ groups: Group[]; last_visit: number }>("/audition" + qs({ category, music_only: musicOnly, since: newOnly ? since : 0 })),
    [category, musicOnly, newOnly, since]);
  const [msg, setMsg] = useState<string | null>(null);
  useEvents(["take", "install"], () => reload());
  // "New since last visit": remember when you were here before; mark this visit when you leave.
  useEffect(() => {
    get("/audition" + qs({ since: 1e12 })).then((r) => setSince(r.last_visit || 0));
    return () => { post("/audition/seen"); };
  }, []);
  const categories = useMemo(() => [...new Set((data?.groups || []).map((g) => g.cue.category))].sort(), [data]);

  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}>
        <h1 style={{ margin: 0 }}>Audition</h1>
        <span className="muted">{data?.groups.reduce((n, g) => n + g.candidates.length, 0) ?? "…"} candidates</span>
        <span className="spacer" />
        <label><input type="checkbox" checked={newOnly} onChange={(e) => setNewOnly(e.target.checked)} /> New since last visit</label>
        <label><input type="checkbox" checked={musicOnly} onChange={(e) => setMusicOnly(e.target.checked)} /> Music only</label>
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {categories.map((c) => <option key={c}>{c}</option>)}
        </select>
      </div>
      <ErrorBox error={error} />
      {msg && <div className="card" style={{ marginBottom: 8 }}>{msg}</div>}
      {data?.groups.map((g, i) => (
        <div key={g.cue.id}>
          {(i === 0 || data.groups[i - 1].cue.category !== g.cue.category) && <h2 style={{ textTransform: "capitalize" }}>{g.cue.category}</h2>}
          <CueGroup group={g} onMessage={setMsg} onChanged={reload} />
        </div>
      ))}
      {data && !data.groups.length && <div className="muted">No candidates to audition. Generate takes from the Cues screen.</div>}
    </div>
  );
}

function CueGroup({ group, onMessage, onChanged }: { group: Group; onMessage: (m: string) => void; onChanged: () => void }) {
  const d = useDeck();
  const [focus, setFocus] = useState<string | null>(null);
  const [ab, setAb] = useState<[string | null, string | null]>([null, null]);
  const urlOf = (t: Take) => workspaceUrl(t.path);
  const focused = group.candidates.find((c) => c.name === focus) || null;

  const keep = async (t: Take, mode: "add" | "replace-generated") => {
    if (mode === "replace-generated" && !confirm(`Remove ${group.cue.id}'s files that the tool installed, then install ${t.name}? Your own files are kept.`)) return;
    try {
      const r = await post("/promote", { candidates: [t.name], mode });
      onMessage(r.installed.length ? `Installed ${t.name} as ${r.installed[0].path}.` : `${t.name} was not installed (see the Engine log).`);
      onChanged();
    } catch (e: any) { onMessage(e.message); }
  };
  const reject = async (t: Take) => { await post("/candidates/reject", { name: t.name }); onChanged(); };
  const abSwitch = () => {
    const [a, b] = ab;
    if (!a || !b) return;
    const urlA = workspaceUrl(group.candidates.find((c) => c.name === a)!.path);
    const urlB = workspaceUrl(group.candidates.find((c) => c.name === b)!.path);
    deck.switchTo(d.url === urlA ? urlB : urlA);
  };

  return (
    <div className="card" style={{ marginBottom: 8 }}>
      <div className="row">
        <b className="mono">{group.cue.id}</b><span className="muted">{group.cue.trigger}</span>
        {group.cue.loop && <span className="pill">loop</span>}{group.cue.locked && <span className="pill warm">locked</span>}
        <span className="spacer" />
        {ab[0] && ab[1] && <button onClick={abSwitch} title="Switch between A and B at the same position">A/B switch</button>}
        <button className="small" onClick={() => nav.go("Cues", { cue: group.cue.id })}>Edit cue</button>
      </div>
      {group.cue.notes && <div className="muted">Notes: {group.cue.notes}</div>}
      <div className="row" style={{ margin: "4px 0" }}>
        <span className="muted">Now in the game:</span>
        {group.installed.length ? group.installed.map((f) => (
          <span key={f.path} className="row"><PlayButton url={gameUrl(f.path)} /><span className="mono">{f.path.split("/").pop()}</span>
            {!f.generated && <span className="pill good">yours</span>}
            <button className="small" onClick={() => reveal(f.path)} title="Show in Explorer">Reveal</button></span>
        )) : <span className="bad">nothing (silence or a placeholder)</span>}
      </div>
      {focused && <Waveform url={urlOf(focused)} />}
      {group.candidates.map((t) => (
        <div key={t.name} className={cx("cand", focus === t.name && "focus", t.installed && "kept")} onClick={() => setFocus(t.name)}>
          <PlayButton url={urlOf(t)} />
          <span className="mono">{t.name} {ab[0] === t.name && <span className="pill">A</span>}{ab[1] === t.name && <span className="pill">B</span>}
            {t.installed && <span className="pill good">installed as {t.installed.split("/").pop()}</span>}</span>
          <span className="muted">{fmtTime(t.seconds)}</span>
          <span className="muted">{t.lufs !== null ? `${t.lufs} LUFS` : ""}</span>
          <span className="muted">{t.tempo ? `${t.tempo.toFixed(0)} BPM` : ""}</span>
          <span className="row" onClick={(e) => e.stopPropagation()}>
            <button className="small" onClick={() => setAb(ab[0] && !ab[1] && ab[0] !== t.name ? [ab[0], t.name] : [t.name, null])}>A/B</button>
            {group.cue.loop && <button className="small" onClick={() => deck.loopPreview(urlOf(t))} title="Play the wrap-around from end to start">Loop</button>}
            {group.cue.kind === "music" && <button className="small" onClick={() => nav.go("Map", { file: t.path })}>Map</button>}
            <button className="small good" disabled={group.cue.locked} onClick={() => keep(t, "add")}>Keep</button>
            <button className="small" disabled={group.cue.locked} onClick={() => keep(t, "replace-generated")}>Replace generated</button>
            <button className="small danger" onClick={() => reject(t)}>Reject</button>
          </span>
        </div>
      ))}
    </div>
  );
}
