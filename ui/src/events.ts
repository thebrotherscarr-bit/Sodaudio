// The event socket (ws /api/events) as a tiny store: components subscribe to event types; the latest health is kept.
import { useEffect, useRef, useState } from "react";
import type { Health } from "./api";

type Listener = (event: any) => void;
const listeners = new Set<Listener>();
let socket: WebSocket | null = null;
let health: Health | null = null;
let connected = false;
const statusListeners = new Set<() => void>();

function notifyStatus() {
  statusListeners.forEach((l) => l());
}

export function connect() {
  if (socket && socket.readyState <= 1) return;
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/events`;
  socket = new WebSocket(url);
  socket.onopen = () => {
    connected = true;
    notifyStatus();
  };
  socket.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.type === "health") {
      health = event;
      notifyStatus();
    }
    listeners.forEach((l) => l(event));
  };
  socket.onclose = () => {
    connected = false;
    notifyStatus();
    setTimeout(connect, 1500);
  };
}

export function onEvent(listener: Listener) {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

// Calls handler for events of the given types; the handler can change between renders.
export function useEvents(types: string[], handler: Listener) {
  const ref = useRef(handler);
  ref.current = handler;
  const key = types.join(",");
  useEffect(() => onEvent((e) => (types.includes(e.type) ? ref.current(e) : undefined)), [key]);
}

export function useConnection(): { connected: boolean; health: Health | null } {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    statusListeners.add(l);
    return () => void statusListeners.delete(l);
  }, []);
  return { connected, health };
}
