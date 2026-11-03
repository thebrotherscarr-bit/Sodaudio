import { useEffect, useRef, useState } from "react";
import { get, patch, post } from "../api";
import { ErrorBox } from "../components/common";

// The project's house sound, choir, templates, chant lines, shapes and shape rules, with three sample cues composed live.
export default function Style() {
  const [project, setProject] = useState<any>(null);
  const [style, setStyle] = useState<Record<string, string>>({});
  const [templates, setTemplates] = useState<Record<string, string>>({});
  const [lines, setLines] = useState("");
  const [shapes, setShapes] = useState("");
  const [rules, setRules] = useState("");
  const [uppercase, setUppercase] = useState(true);
  const [samples, setSamples] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const timer = useRef<any>();

  const load = () => get("/project").then((p) => {
    setProject(p);
    setStyle(p.style || {});
    setTemplates(p.templates || {});
    setLines((p.chant?.lines || []).join("\n"));
    setShapes(JSON.stringify(p.chant?.shapes || {}, null, 2));
    setRules(JSON.stringify(p.chant?.shape_rules || [], null, 2));
    setUppercase(p.chant?.uppercase_choruses ?? true);
  });
  useEffect(() => { load(); }, []);

  const chant = () => {
    if (!lines.trim()) return null;
    return { lines: lines.split("\n").map((l) => l.trim()).filter(Boolean), shapes: JSON.parse(shapes), shape_rules: JSON.parse(rules), uppercase_choruses: uppercase };
  };

  useEffect(() => {
    if (!project) return;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      try {
        post("/preview", { project: { style, templates, chant: chant() } }).then((r) => { setSamples(r.samples); setErr(null); }).catch((e) => setErr(e.message));
      } catch (e: any) { setErr("Shapes or rules are not valid JSON: " + e.message); }
    }, 300);
  }, [style, templates, lines, shapes, rules, uppercase, project]);

  const save = async () => {
    try {
      await patch("/project", { style, templates, chant: chant() });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) { setErr(e.message); }
  };

  if (!project) return <div className="muted">Loading…</div>;
  return (
    <div>
      <div className="row"><h1 style={{ margin: 0 }}>Style</h1><span className="spacer" />{saved && <span className="good">Saved</span>}
        <button className="primary" onClick={save}>Save style</button></div>
      <p className="muted">Fields here fill the templates for every cue without its own prompt. Changes are recorded and can be undone on the Cues screen.</p>
      <ErrorBox error={err} />
      <div className="split">
        <div className="col">
          <h2>House sound and voices</h2>
          {Object.keys({ house_sound: 1, choir: 1, music_suffix: 1, sfx_suffix: 1, ambience_suffix: 1, ...style }).map((k) => (
            <div key={k}><label>{k}</label><textarea rows={2} value={style[k] || ""} onChange={(e) => setStyle({ ...style, [k]: e.target.value })} /></div>
          ))}
          <h2>Templates</h2>
          <p className="muted">Fields: {"{card} {voice} {context} {trigger}"} and any style field above, such as {"{house_sound}"}.</p>
          {Object.keys({ music: 1, sfx: 1, ambience: 1, ...templates }).map((k) => (
            <div key={k}><label>{k}</label><textarea rows={2} value={templates[k] || ""} onChange={(e) => setTemplates({ ...templates, [k]: e.target.value })} /></div>
          ))}
          <h2>Chant</h2>
          <label>Lines, one per line (invented syllables only)</label>
          <textarea rows={6} value={lines} onChange={(e) => setLines(e.target.value)} />
          <label><input type="checkbox" checked={uppercase} onChange={(e) => setUppercase(e.target.checked)} /> Capitalise chorus lines (not in calm shapes)</label>
          <label>Shapes (section tags per shape, JSON)</label>
          <textarea rows={12} value={shapes} onChange={(e) => setShapes(e.target.value)} />
          <label>Shape rules (first match wins, JSON)</label>
          <textarea rows={8} value={rules} onChange={(e) => setRules(e.target.value)} />
        </div>
        <div className="sticky col">
          <h2>Preview</h2>
          {samples.map((s) => (
            <div key={s.id} className="card col">
              <div className="row"><b className="mono">{s.id}</b><span className="pill">{s.shape}</span></div>
              <div className="final">{s.prompt_final}</div>
              {s.plan_final && <div className="final">{s.plan_final}</div>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
