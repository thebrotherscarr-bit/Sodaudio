// The small native API the UI sees as window.native (contextIsolation on, no Node in the page). Everything else goes
// to the engine's HTTP API.
import { contextBridge, ipcRenderer, webUtils } from "electron";

contextBridge.exposeInMainWorld("native", {
  pickFolder: () => ipcRenderer.invoke("pick-folder"),
  pickFile: (filters?: { name: string; extensions: string[] }[]) => ipcRenderer.invoke("pick-file", filters),
  pathForFile: (file: File) => webUtils.getPathForFile(file),
  reveal: (p: string) => ipcRenderer.invoke("reveal", p),
  notify: (title: string, body: string, jobId?: number) => ipcRenderer.invoke("notify", title, body, jobId),
  setProgress: (value: number) => ipcRenderer.invoke("set-progress", value),
  setStartWithWindows: (enabled: boolean) => ipcRenderer.invoke("start-with-windows", enabled),
  setApiKey: (key: string) => ipcRenderer.invoke("set-api-key", key),
  onOpenJob: (callback: (jobId: number) => void) => ipcRenderer.on("open-job", (_e, id) => callback(id)),
});
