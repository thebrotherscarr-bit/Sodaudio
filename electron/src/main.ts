// Sodaudio's Electron shell: starts the engine, shows the engine's own page in a window, and adds native
// features (real paths, folder pickers, notifications, taskbar progress, a tray, auto-update).
import { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, Notification, powerSaveBlocker, safeStorage, shell, Tray } from "electron";
import * as fs from "node:fs";
import * as path from "node:path";
import { BASE, Engine, request } from "./engine";

// The engine keeps its settings and logs in %APPDATA%\Sodaudio; the desktop shell keeps its own beside them.
app.setPath("userData", path.join(app.getPath("appData"), "Sodaudio", "desktop"));

const userData = app.getPath("userData");
const engine = new Engine();
let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let quitting = false;
let awakeId: number | null = null;
let health: any = null;
let settings: any = {};
let updateReady: string | null = null; // a downloaded update's version: it installs when the app quits

// --- single instance ----------------------------------------------------------------------------------------------
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => showWindow());
}

// --- the API key: safeStorage, handed to the engine through its environment, never written in plain text ------------
const keyFile = path.join(userData, "api-key.bin");
function loadKey(): string | null {
  try { return safeStorage.decryptString(fs.readFileSync(keyFile)); } catch { return null; }
}
function applyKey() {
  const key = loadKey();
  if (key) engine.env.ANTHROPIC_API_KEY = key;
  else delete engine.env.ANTHROPIC_API_KEY;
}

// --- window state -------------------------------------------------------------------------------------------------
const stateFile = path.join(userData, "window.json");
function loadBounds() {
  try { return JSON.parse(fs.readFileSync(stateFile, "utf8")); } catch { return { width: 1440, height: 900 }; }
}
function saveBounds() {
  if (!win || win.isMinimized()) return;
  fs.writeFileSync(stateFile, JSON.stringify({ ...win.getNormalBounds(), maximized: win.isMaximized() }));
}

function iconPath() {
  return app.isPackaged ? path.join(process.resourcesPath, "engine", "ui", "dist", "icon-192.png") : path.resolve(__dirname, "..", "..", "ui", "public", "icon-192.png");
}

function createWindow() {
  const bounds = loadBounds();
  win = new BrowserWindow({
    ...bounds, minWidth: 900, minHeight: 600, show: false, title: "Sodaudio", backgroundColor: "#16191f", icon: iconPath(),
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  if (bounds.maximized) win.maximize();
  win.removeMenu();
  win.once("ready-to-show", () => { if (!process.argv.includes("--hidden")) win?.show(); });
  win.on("resize", saveBounds);
  win.on("move", saveBounds);
  // Closing keeps the app in the tray while jobs run (a setting); otherwise it quits.
  win.on("close", (event) => {
    if (quitting) return;
    if (settings.keep_in_tray !== false && (health?.busy || health?.queued)) {
      event.preventDefault();
      win?.hide();
      if (Notification.isSupported()) new Notification({ title: "Sodaudio", body: "Still running in the tray: the queue keeps going." }).show();
    }
  });
  win.on("closed", () => { win = null; if (!quitting && !(health?.busy || health?.queued)) app.quit(); });
  // Links to other sites open in the default browser, not in the app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(BASE)) return { action: "allow" };
    shell.openExternal(url);
    return { action: "deny" };
  });
  win.loadURL(process.env.AD_DEV_URL || BASE + "/");
}

function showWindow() {
  if (!win) createWindow();
  win?.show();
  if (win?.isMinimized()) win.restore();
  win?.focus();
}

// --- tray ---------------------------------------------------------------------------------------------------------
function trayTitle() {
  if (!health) return "Engine not answering";
  if (health.running) return `#${health.running.id} ${health.running.type} ${Math.round((health.running.progress || 0) * 100)}%`;
  return `${health.paused ? "Paused, " : ""}${health.queued || 0} queued`;
}
function updateTray() {
  if (!tray) return;
  tray.setToolTip("Sodaudio: " + trayTitle());
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: trayTitle(), enabled: false },
    { type: "separator" },
    { label: "Open", click: showWindow },
    { label: health?.paused ? "Resume queue" : "Pause queue", click: () => request("POST", health?.paused ? "/api/queue/resume" : "/api/queue/pause", {}).catch(() => {}) },
    ...(updateReady ? [{ label: `Restart to update to ${updateReady}`, click: () => quit({ restart: true }) }] : []),
    { label: "Quit", click: () => quit() },
  ]));
}

// Every few seconds: the queue state for the tray, taskbar progress and keeping the PC awake while jobs run.
async function poll() {
  try {
    health = await request("GET", "/api/health");
    settings = await request("GET", "/api/settings");
  } catch { health = null; }
  updateTray();
  const busy = !!(health?.busy || health?.queued);
  if (busy && settings.keep_awake !== false && awakeId === null) awakeId = powerSaveBlocker.start("prevent-app-suspension");
  if ((!busy || settings.keep_awake === false) && awakeId !== null) { powerSaveBlocker.stop(awakeId); awakeId = null; }
  if (!busy) win?.setProgressBar(-1);
  else if (health?.running) win?.setProgressBar(Math.max(0.01, health.running.progress || 0));
}

// --- native calls for the UI --------------------------------------------------------------------------------------
ipcMain.handle("pick-folder", async () => {
  const r = await dialog.showOpenDialog(win!, { properties: ["openDirectory"] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("pick-file", async (_e, filters) => {
  const r = await dialog.showOpenDialog(win!, { properties: ["openFile"], filters: filters || [] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("reveal", async (_e, target: string) => {
  // Paths from the UI may be game-relative; the engine knows the project root.
  let full = target;
  if (!path.isAbsolute(full)) {
    try { full = (await request("POST", "/api/reveal", { path: target, open: false })).path; } catch { /* fall through */ }
  }
  shell.showItemInFolder(full);
});
ipcMain.handle("notify", (_e, title: string, body: string, jobId?: number) => {
  if (!Notification.isSupported()) return;
  const note = new Notification({ title, body, icon: iconPath() });
  note.on("click", () => { showWindow(); if (jobId) win?.webContents.send("open-job", jobId); });
  note.show();
});
ipcMain.handle("set-progress", (_e, value: number) => win?.setProgressBar(value < 0 ? -1 : value));
ipcMain.handle("start-with-windows", async (_e, enabled: boolean) => {
  app.setLoginItemSettings({ openAtLogin: enabled, args: ["--hidden"] });
  await request("PUT", "/api/settings", { start_with_windows: enabled }).catch(() => {});
});
ipcMain.handle("update-state", () => ({ version: app.getVersion(), ready: updateReady }));
ipcMain.handle("restart-to-update", () => quit({ restart: true }));
ipcMain.handle("set-api-key", async (_e, key: string) => {
  if (key) fs.writeFileSync(keyFile, safeStorage.encryptString(key));
  else if (fs.existsSync(keyFile)) fs.unlinkSync(keyFile);
  applyKey();
  await engine.restart();
});

// --- self-updating --------------------------------------------------------------------------------------------------
// Installed copies check this repository's GitHub Releases at start and every few hours, download a new version in
// the background, and install it when the app quits (or at once, from the tray or the app's banner). The engine is
// stopped first, like any quit, so a running job is never cut off without asking. Settings and projects live in
// %APPDATA%\Sodaudio, outside the install folder, so an update keeps them.
const UPDATE_EVERY = 4 * 3600 * 1000;
let updater: any = null;

async function setupUpdates() {
  if (!app.isPackaged || process.env.SODAUDIO_NO_UPDATES) return;
  try {
    updater = (await import("electron-updater")).autoUpdater;
  } catch (e: any) { engine.write("no auto-update: " + e.message); return; }
  updater.autoDownload = true;
  updater.autoInstallOnAppQuit = false; // installed by quit() below, after the engine has stopped
  updater.logger = { info: (m: any) => engine.write("update: " + m), warn: (m: any) => engine.write("update: " + m),
                     error: (m: any) => engine.write("update error: " + m), debug: () => {} };
  updater.on("update-available", (info: any) => engine.write(`update: ${info.version} available; downloading`));
  updater.on("update-not-available", () => engine.write(`update: ${app.getVersion()} is the latest`));
  updater.on("error", (e: Error) => engine.write("update check failed: " + e.message));
  updater.on("update-downloaded", (info: any) => {
    updateReady = info.version;
    engine.write(`update: ${info.version} downloaded; installs when Sodaudio quits`);
    updateTray();
    win?.webContents.send("update-ready", info.version);
    if (Notification.isSupported()) {
      const note = new Notification({ title: `Sodaudio ${info.version} is ready`, body: "It installs when you quit. Click to restart now.", icon: iconPath() });
      note.on("click", () => quit({ restart: true }));
      note.show();
    }
  });
  const check = () => { if (!updateReady) updater.checkForUpdates().catch((e: Error) => engine.write("update check failed: " + e.message)); };
  check();
  setInterval(check, UPDATE_EVERY);
}

// --- quitting -----------------------------------------------------------------------------------------------------
async function quit(options: { restart?: boolean } = {}) {
  if (quitting) return;
  let mode: "finish" | "stop" = "finish";
  if (!engine.external && health?.busy) {
    const answer = dialog.showMessageBoxSync({
      type: "question", buttons: ["Finish the current job, then quit", "Stop it now and quit", "Cancel"], defaultId: 0, cancelId: 2,
      title: "A job is running", message: `Job #${health.running?.id} (${health.running?.type}) is running.`,
    });
    if (answer === 2) return;
    mode = answer === 0 ? "finish" : "stop";
  }
  quitting = true;
  win?.hide();
  tray?.destroy();
  await engine.stop(mode);
  if (updateReady && updater && !process.env.SODAUDIO_TEST_NO_INSTALL) {
    engine.write(`update: installing ${updateReady}${options.restart ? " and restarting" : ""}`);
    updater.quitAndInstall(true, !!options.restart); // silent install; relaunch only when asked
    return;
  }
  app.exit(0);
}

app.on("before-quit", (event) => { if (!quitting) { event.preventDefault(); quit(); } });
app.on("window-all-closed", () => { /* the tray keeps the app alive; quit() ends it */ });

app.whenReady().then(async () => {
  tray = new Tray(nativeImage.createFromPath(iconPath()).resize({ width: 16, height: 16 }));
  tray.on("click", showWindow);
  updateTray();
  engine.onFatal = (message) => dialog.showErrorBox("Sodaudio engine", message);
  applyKey();
  try {
    await engine.start();
  } catch (e: any) {
    dialog.showErrorBox("Sodaudio", e.message);
    quitting = true;
    app.exit(1);
    return;
  }
  createWindow();
  // Tests only: quit by itself after this many milliseconds (the quit path is otherwise a menu click).
  if (process.env.AD_TEST_QUIT_AFTER) setTimeout(() => quit(), Number(process.env.AD_TEST_QUIT_AFTER));
  setInterval(poll, 4000);
  poll();
  setupUpdates();
});
