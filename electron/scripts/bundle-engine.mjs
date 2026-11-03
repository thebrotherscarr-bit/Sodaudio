// Bundles the engine for the installer: a portable Python 3.12 (uv's standalone build) with the engine's packages
// installed into it, the engine code and the built UI, in electron/build/engine (resources/engine once installed).
// ComfyUI, torch and the models are never bundled; the Setup screen finds or installs them.
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, lstatSync, mkdirSync, readdirSync, renameSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const tool = resolve(here, "..", "..");
const out = resolve(here, "..", "build", "engine");
const run = (cmd, args, cwd) => execFileSync(cmd, args, { stdio: "inherit", cwd });

if (!existsSync(join(tool, "ui", "dist", "index.html"))) throw new Error("build the UI first: npm run build in ui/");
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

// A private Python 3.12 for the bundle: uv's standalone builds are relocatable, unlike a venv. It is installed straight
// into the build folder, so no Python already on this PC (shared installs, venvs) is used or changed.
const download = resolve(here, "..", "build", "python-download");
rmSync(download, { recursive: true, force: true });
run("uv", ["python", "install", "3.12", "--install-dir", download, "--no-bin", "--no-registry"]);
// The real folder (cpython-3.12.14-…), not uv's version link to it (cpython-3.12-…, a junction).
const installed = readdirSync(download).find((name) => /^cpython-3\.12\.\d+-windows/.test(name) && !lstatSync(join(download, name)).isSymbolicLink()
  && existsSync(join(download, name, "python.exe")));
if (!installed) throw new Error("uv did not install Python 3.12 into " + download);
renameSync(join(download, installed), join(out, "python"));
rmSync(download, { recursive: true, force: true });
const bundled = join(out, "python", "python.exe");
// Guard: packages must go into the bundle's own Python, never anywhere else.
const prefix = execFileSync(bundled, ["-c", "import sys; print(sys.prefix)"], { encoding: "utf8" }).trim();
if (resolve(prefix).toLowerCase() !== resolve(out, "python").toLowerCase()) throw new Error(`bundled Python reports prefix ${prefix}; refusing to install`);
run("uv", ["pip", "install", "--python", bundled, "--break-system-packages", "--no-config", "-r", join(tool, "engine", "requirements.txt")]);

cpSync(join(tool, "engine"), join(out, "engine"), {
  recursive: true,
  filter: (src) => !/[\\/](tests|__pycache__|\.pytest_cache)([\\/]|$)/.test(src),
});
cpSync(join(tool, "ui", "dist"), join(out, "ui", "dist"), { recursive: true });
run(bundled, ["-c", "import engine.app, librosa, fastapi; print('bundled engine imports')"], out);
console.log("engine bundled in", out);
