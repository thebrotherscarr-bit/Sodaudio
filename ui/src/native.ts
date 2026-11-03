// Native features from Electron's preload script (window.native). In a browser tab or PWA it is missing, and each
// caller falls back to the engine's own way (folder browser, uploads, Explorer through the engine).
import { post } from "./api";

export interface Native {
  pickFolder(): Promise<string | null>;
  pickFile(filters?: { name: string; extensions: string[] }[]): Promise<string | null>;
  pathForFile(file: File): string;
  reveal(path: string): Promise<void>;
  notify(title: string, body: string, jobId?: number): Promise<void>;
  setProgress(value: number): Promise<void>;
  onOpenJob?(callback: (jobId: number) => void): void;
  updateState?(): Promise<{ version: string; ready: string | null }>;
  onUpdateReady?(callback: (version: string) => void): void;
  restartToUpdate?(): Promise<void>;
}

declare global {
  interface Window {
    native?: Native;
  }
}

export const native = (): Native | undefined => window.native;

export async function reveal(path: string) {
  const n = native();
  if (n) return n.reveal(path);
  await post("/reveal", { path });
}

// Desktop notification: native in Electron, the Notifications API in a browser or installed PWA.
export async function notify(title: string, body: string, jobId?: number) {
  const n = native();
  if (n) return n.notify(title, body, jobId);
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") await Notification.requestPermission();
  if (Notification.permission === "granted") new Notification(title, { body, icon: "/icon-192.png" });
}

export function setTaskbarProgress(value: number) {
  native()?.setProgress(value);
}
