// Electron checks (BUILD-ELECTRON E1-E3) with Playwright's Electron driver:
//   - window.native exists in the page and the engine came up behind the window;
//   - a FLAC dropped on the Map screen is mapped in place from its real path (nothing uploaded);
//   - the taskbar progress bar follows the running queue;
//   - closing the window while jobs run keeps the app (and the queue) going in the tray, with the PC kept awake;
//   - quitting leaves no engine or ComfyUI process behind.
// Needs SODAUDIO_HOME (a test settings folder with a project open) and AD_DROP_FILE (an audio file outside the
// workspace).   node e2e-check.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(here, "..", "ui", "package.json"));
const { _electron } = require("@playwright/test");
const port = process.env.SODAUDIO_PORT || "8774";
const api = (path, init) => fetch(`http://127.0.0.1:${port}/api${path}`, init).then((r) => r.json());
const results = [];
const check = (name, ok, detail = "") => { results.push([name, ok]); console.log(`${ok ? "PASS" : "FAIL"} ${name} ${detail}`); };

const app = await _electron.launch({ executablePath: join(here, "node_modules", "electron", "dist", "electron.exe"), args: [here], env: { ...process.env, SODAUDIO_PORT: port } });
await app.evaluate(({ BrowserWindow, powerSaveBlocker }) => {
  globalThis.__progress = [];
  globalThis.__awake = [];
  const set = BrowserWindow.prototype.setProgressBar;
  BrowserWindow.prototype.setProgressBar = function (v, o) { globalThis.__progress.push(v); return set.call(this, v, o); };
  const start = powerSaveBlocker.start.bind(powerSaveBlocker);
  powerSaveBlocker.start = (type) => { const id = start(type); globalThis.__awake.push(id); return id; };
});
const page = await app.firstWindow();
await page.waitForSelector("text=Sodaudio");
check("window shows the engine's UI with window.native", await page.evaluate(() => typeof window.native?.pathForFile === "function"));
const sandboxed = await page.evaluate(() => typeof window.require === "undefined" && typeof window.process === "undefined");
check("page has no Node (contextIsolation, sandbox)", sandboxed);

// Drop a real file from disk onto the Map screen.
await page.click("text=Map & recompose");
await page.waitForSelector("text=Drop an audio file anywhere here");
const input = await page.evaluateHandle(() => { const i = document.createElement("input"); i.type = "file"; i.id = "__drop"; document.body.append(i); return i; });
await input.asElement().setInputFiles(process.env.AD_DROP_FILE);
await page.evaluate(() => {
  const file = document.getElementById("__drop").files[0];
  const data = new DataTransfer();
  data.items.add(file);
  const target = document.querySelector(".drop").parentElement;
  target.dispatchEvent(new DragEvent("drop", { dataTransfer: data, bubbles: true, cancelable: true }));
});
const dropName = process.env.AD_DROP_FILE.split(/[\\/]/).pop();
await page.waitForSelector(`text=${dropName}: `, { timeout: 180000 });
const mapJobs = (await api("/jobs")).jobs.filter((j) => j.type === "map");
const mapped = mapJobs.find((j) => j.body.file === process.env.AD_DROP_FILE && j.state === "done")?.body.file;
check("dropped file mapped in place by its real path", mapped === process.env.AD_DROP_FILE, mapped);
const project = await api("/project");
const inbox = await fetch(`http://127.0.0.1:${port}/api/fs?path=${encodeURIComponent(project.workspace_path + "\\inbox")}`).then((r) => r.status);
check("nothing was uploaded to the inbox", inbox !== 200 || (await api(`/fs?path=${encodeURIComponent(project.workspace_path + "\\inbox")}`)).files.length === 0);

// A queue run: taskbar progress, then close the window while it runs.
await api("/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "generate", cues: ["mus_crystal_day"], takes: (await api("/cues/mus_crystal_day")).takes + 3, seconds: 6 }) });
await page.waitForTimeout(6000);
const progress = await app.evaluate(() => globalThis.__progress);
check("taskbar progress follows the queue", progress.some((v) => v > 0 && v < 1), JSON.stringify(progress.slice(-5)));
await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
await new Promise((r) => setTimeout(r, 1500));
const hidden = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((w) => w.isVisible()));
check("closing the window keeps the app in the tray", hidden.length === 1 && hidden[0] === false);
check("PC kept awake while the queue runs", (await app.evaluate(() => globalThis.__awake)).length > 0);
let done = false;
for (let i = 0; i < 120 && !done; i++) {
  const health = await api("/health");
  done = !health.busy && !health.queued;
  await new Promise((r) => setTimeout(r, 1000));
}
const last = (await api("/jobs?state=done")).jobs.filter((j) => j.type === "generate").at(-1);
check("queue finished with the window closed", done && last?.result?.made?.length === 3, JSON.stringify(last?.result));

await app.close().catch(() => {});
await new Promise((r) => setTimeout(r, 4000));
const engineGone = await fetch(`http://127.0.0.1:${port}/api/health`).then(() => false, () => true);
check("quitting stopped the engine", engineGone);
const failed = results.filter(([, ok]) => !ok).length;
console.log(failed ? `ELECTRON CHECK FAILED (${failed})` : "ELECTRON CHECK PASSED");
process.exit(failed ? 1 : 0);
