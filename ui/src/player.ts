// One shared audio deck for the whole app, so starting a take stops the last one, and A/B can switch between two
// takes at the same position.
import { useEffect, useState } from "react";

const audio = new Audio();
audio.preload = "auto";
let current: string | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());
["play", "pause", "ended", "timeupdate"].forEach((e) => audio.addEventListener(e, notify));

export const deck = {
  audio,
  get url() { return current; },
  get playing() { return !audio.paused && !audio.ended; },
  async play(url: string, opts: { from?: number; loop?: boolean } = {}) {
    if (current !== url) {
      current = url;
      audio.src = url;
      await new Promise<void>((resolve) => {
        const ok = () => { audio.removeEventListener("loadedmetadata", ok); resolve(); };
        audio.addEventListener("loadedmetadata", ok);
        audio.load();
      });
    }
    audio.loop = !!opts.loop;
    if (opts.from !== undefined) audio.currentTime = Math.max(0, opts.from);
    await audio.play();
    notify();
  },
  stop() { audio.pause(); notify(); },
  toggle(url: string) { return this.playing && current === url ? this.stop() : this.play(url); },
  // A/B: carry on at the same position in the other take.
  async switchTo(url: string) {
    const at = audio.currentTime;
    const wasPlaying = this.playing;
    await this.play(url, { from: at });
    if (!wasPlaying) audio.pause();
  },
  // Loops: start a few seconds before the end so you hear the wrap-around into the head.
  async loopPreview(url: string, lead = 3) {
    await this.play(url, { loop: true });
    audio.currentTime = Math.max(0, (audio.duration || lead) - lead);
  },
  seek(t: number) { audio.currentTime = t; notify(); },
};

export function useDeck() {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    listeners.add(l);
    return () => void listeners.delete(l);
  }, []);
  return deck;
}
