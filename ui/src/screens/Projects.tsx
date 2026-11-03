import { useState } from "react";
import { get, post } from "../api";
import { ErrorBox, FolderBrowser } from "../components/common";
import { native } from "../native";
import { nav } from "../App";
import { useLoad } from "../util";

export default function Projects() {
  const { data, error, reload } = useLoad(() => get<any[]>("/projects"));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [browse, setBrowse] = useState<null | "open" | "new">(null);
  const [form, setForm] = useState({ folder: "", name: "", importer: "folder-scan", command: "", format: "rimtera-tsv", output: "" });

  const open = async (path: string) => {
    setBusy(true);
    try {
      await post("/projects/open", { path });
      nav.go("Cues");
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); reload(); }
  };

  const pickProjectFile = async () => {
    const n = native();
    if (n) {
      const path = await n.pickFile([{ name: "Audio project", extensions: ["json"] }]);
      if (path) open(path);
    } else setBrowse("open");
  };

  const pickGameFolder = async () => {
    const n = native();
    if (n) {
      const path = await n.pickFolder();
      if (path) setForm((f) => ({ ...f, folder: path, name: f.name || path.split(/[\\/]/).pop() || "" }));
    } else setBrowse("new");
  };

  const create = async () => {
    setBusy(true);
    try {
      const importer = form.importer === "command"
        ? { type: "command", command: form.command, format: form.format, output: form.output }
        : { type: form.importer };
      await post("/projects/open", { path: form.folder.replace(/[\\/]$/, "") + "/audio-project.json", create: true, name: form.name, importer });
      if (form.importer !== "manual") await post("/project/import", { run_command: form.importer === "command" });
      nav.go("Cues");
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <div>
      <h1>Projects</h1>
      <ErrorBox error={error || err} />
      <div className="row">
        <button className="primary" onClick={pickProjectFile} disabled={busy}>Open project file…</button>
      </div>
      <h2>Recent</h2>
      <table className="grid">
        <thead><tr><th>Project</th><th>Cues</th><th>No sound</th><th>File</th><th /></tr></thead>
        <tbody>
          {data?.map((p) => (
            <tr key={p.path} onClick={() => p.exists && open(p.path)}>
              <td><b>{p.name || "?"}</b> {p.open && <span className="pill good">open</span>}</td>
              <td>{p.cues ?? "–"}</td>
              <td>{p.no_sound ?? "–"}</td>
              <td className="mono muted">{p.path}{!p.exists && <span className="bad"> (missing)</span>}{p.error && <span className="bad"> {p.error}</span>}</td>
              <td><button className="small" disabled={!p.exists || busy}>Open</button></td>
            </tr>
          ))}
          {data && !data.length && <tr><td colSpan={5} className="muted">No projects yet.</td></tr>}
        </tbody>
      </table>

      <h2>New project</h2>
      <div className="card col" style={{ maxWidth: 720 }}>
        <div className="row">
          <label>Game folder</label>
          <input className="grow mono" value={form.folder} onChange={(e) => setForm({ ...form, folder: e.target.value })} placeholder="C:\Games\MyGame" />
          <button onClick={pickGameFolder}>Pick…</button>
        </div>
        <div className="row"><label>Name</label><input className="grow" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div className="row">
          <label>Importer</label>
          <select value={form.importer} onChange={(e) => setForm({ ...form, importer: e.target.value })}>
            <option value="folder-scan">Scan the game's audio folders</option>
            <option value="command">Run a command that lists the cues</option>
            <option value="manual">None: add cues by hand</option>
          </select>
        </div>
        {form.importer === "command" && (
          <>
            <div className="row"><label>Command</label><input className="grow mono" value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })} placeholder="dotnet run -- --sound-cues" /></div>
            <div className="row">
              <label>Format</label>
              <select value={form.format} onChange={(e) => setForm({ ...form, format: e.target.value })}>
                <option value="rimtera-tsv">rimtera-tsv</option><option value="csv">csv</option><option value="json">json</option>
              </select>
              <label>Output file (if the command writes one)</label>
              <input className="grow mono" value={form.output} onChange={(e) => setForm({ ...form, output: e.target.value })} placeholder="tools/audio/audio_needs.tsv" />
            </div>
          </>
        )}
        <div className="row"><span className="spacer" /><button className="primary" disabled={!form.folder || busy} onClick={create}>Create project</button></div>
        <div className="muted">The project file is saved as audio-project.json in the game folder; generated takes go to audio-work/ next to it (keep it out of git).</div>
      </div>
      {browse && (
        <FolderBrowser mode={browse === "open" ? "file" : "folder"} onClose={() => setBrowse(null)}
          onPick={(path) => {
            setBrowse(null);
            if (browse === "open") open(path);
            else setForm((f) => ({ ...f, folder: path, name: f.name || path.split(/[\\/]/).pop() || "" }));
          }} />
      )}
    </div>
  );
}
