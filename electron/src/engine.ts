// Starts, watches, restarts and stops the engine (a Python child process). Electron runs no Python itself.
import { ChildProcess, execFile, spawn } from "node:child_process";
import { app } from "electron";
import * as fs from "node:fs";
import * as http from "node:http";
import * as path from "node:path";

export const PORT = Number(process.env.SODAUDIO_PORT || 8770);
export const BASE = `http://127.0.0.1:${PORT}`;

export function request(method: string, route: string, body?: unknown, timeout = 3000): Promise<any> {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? undefined : JSON.stringify(body);
    const req = http.request(BASE + route, { method, timeout, headers: data ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) } : {} }, (res) => {
      let text = "";
      res.setEncoding("utf8");
      res.on("data", (c) => (text += c));
      res.on("end", () => {
        if ((res.statusCode || 500) >= 400) return reject(new Error(`${res.statusCode} ${text}`));
        try { resolve(text ? JSON.parse(text) : null); } catch { resolve(text); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

// Where the engine lives: bundled in resources/engine when packaged, or the source checkout in development.
function engineLayout() {
  if (app.isPackaged) {
    const root = path.join(process.resourcesPath, "engine");
    return { cwd: root, python: path.join(root, "python", "python.exe") };
  }
  const root = path.resolve(__dirname, "..", "..");
  return { cwd: root, python: path.join(root, ".venv", "Scripts", "python.exe") };
}

export class Engine {
  child: ChildProcess | null = null;
  external = false; // an engine was already answering (start.cmd): use it, never stop it
  private restarts: number[] = [];
  private stopping = false;
  private log: fs.WriteStream;
  onFatal: (message: string) => void = () => {};
  env: Record<string, string> = {};

  constructor() {
    const dir = path.join(app.getPath("userData"), "logs");
    fs.mkdirSync(dir, { recursive: true });
    this.log = fs.createWriteStream(path.join(dir, "engine-process.log"), { flags: "a" });
  }

  async healthy() {
    try { return !!(await request("GET", "/api/health", undefined, 1500))?.ok; } catch { return false; }
  }

  async start(): Promise<void> {
    if (await this.healthy()) {
      this.external = true;
      this.write("an engine already answers on " + BASE + "; using it");
      return;
    }
    this.spawn();
    await this.waitHealthy(240000);
  }

  private spawn() {
    const { cwd, python } = engineLayout();
    if (!fs.existsSync(python)) throw new Error(`The engine's Python is missing: ${python}`);
    this.write(`starting ${python} -m engine.app --port ${PORT} in ${cwd}`);
    const child = spawn(python, ["-m", "engine.app", "--port", String(PORT)], {
      cwd, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1", ...this.env },
    });
    this.child = child;
    // Child output is read as UTF-8 lines; stderr lines are log lines (libraries warn there), not errors.
    for (const stream of [child.stdout, child.stderr]) {
      let rest = "";
      stream?.setEncoding("utf8");
      stream?.on("data", (chunk: string) => {
        const lines = (rest + chunk).split(/\r?\n/);
        rest = lines.pop() || "";
        lines.forEach((l) => l && this.write(l));
      });
    }
    child.on("exit", (code) => {
      this.write(`engine exited with code ${code}`);
      if (this.child === child) this.child = null;
      if (!this.stopping) this.restartAfterCrash();
    });
  }

  // Restart an engine that exits on its own, at most 3 times in a minute; then show the error.
  private restartAfterCrash() {
    const now = Date.now();
    this.restarts = this.restarts.filter((t) => now - t < 60000);
    if (this.restarts.length >= 3) {
      this.onFatal("The engine stopped three times in a minute. See the log: " + path.join(app.getPath("userData"), "logs", "engine-process.log"));
      return;
    }
    this.restarts.push(now);
    try { this.spawn(); } catch (e: any) { this.onFatal(e.message); }
  }

  async waitHealthy(timeoutMs: number) {
    const until = Date.now() + timeoutMs;
    while (Date.now() < until) {
      if (await this.healthy()) return;
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error("The engine did not answer in time. See " + path.join(app.getPath("userData"), "logs", "engine-process.log"));
  }

  // Restart with new environment (the API key), unless the engine is someone else's.
  async restart() {
    if (this.external) return;
    await this.stop("finish");
    this.stopping = false;
    this.spawn();
    await this.waitHealthy(240000);
  }

  // Ask the engine to exit (finish or stop the current job), then end its whole process tree: no Python, ComfyUI or
  // MOSS process is left behind.
  async stop(mode: "finish" | "stop") {
    this.stopping = true;
    if (this.external || !this.child) return;
    const child = this.child;
    try { await request("POST", "/api/shutdown", { mode }); } catch { /* already gone */ }
    const exited = await new Promise<boolean>((resolve) => {
      if (child.exitCode !== null) return resolve(true);
      const timer = setTimeout(() => resolve(false), mode === "finish" ? 6 * 3600 * 1000 : 30000);
      child.once("exit", () => { clearTimeout(timer); resolve(true); });
    });
    if (child.pid) await new Promise((r) => execFile("taskkill", ["/T", "/F", "/PID", String(child.pid)], () => r(null)));
    this.write(exited ? "engine stopped" : "engine did not exit in time; its process tree was ended");
  }

  write(line: string) {
    this.log.write(`${new Date().toISOString()} ${line}\n`);
  }
}
