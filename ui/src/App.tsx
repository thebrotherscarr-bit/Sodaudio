import { useEffect, useState } from "react";
import { get, Health } from "./api";
import { connect, useConnection } from "./events";
import AgentPanel from "./screens/AgentPanel";
import Audition from "./screens/Audition";
import Cues from "./screens/Cues";
import MapScreen from "./screens/MapScreen";
import Projects from "./screens/Projects";
import QueueScreen from "./screens/Queue";
import Setup from "./screens/Setup";
import Style from "./screens/Style";
import StatusBar from "./components/StatusBar";
import { native } from "./native";

const SCREENS = ["Projects", "Cues", "Style", "Queue", "Audition", "Map", "Setup"] as const;
export type Screen = (typeof SCREENS)[number];

// Cross-screen navigation: "open the map for this take", "show this job".
export const nav = { go: (_s: Screen, _arg?: any) => {} };

export default function App() {
  const { connected, health } = useConnection();
  const [screen, setScreen] = useState<Screen>(() => (localStorage.getItem("screen") as Screen) || "Projects");
  const [arg, setArg] = useState<any>(null);
  const [engineDown, setEngineDown] = useState(false);
  nav.go = (s, a) => { setScreen(s); setArg(a ?? null); };

  useEffect(() => { connect(); }, []);
  useEffect(() => { localStorage.setItem("screen", screen); }, [screen]);
  useEffect(() => { native()?.onOpenJob?.((id) => nav.go("Queue", { job: id })); }, []);
  // The page is cached by the service worker, so it opens even when the engine is off: say so.
  useEffect(() => {
    if (connected) return setEngineDown(false);
    const timer = setTimeout(() => get<Health>("/health").then(() => setEngineDown(false)).catch(() => setEngineDown(true)), 2500);
    return () => clearTimeout(timer);
  }, [connected]);

  if (engineDown && !connected) return <EngineDown />;
  const project = health?.project;
  return (
    <div className="app">
      <nav className="nav">
        <div className="brand">Sodaudio<small>{project || "no project open"}</small></div>
        {SCREENS.map((s) => (
          <button key={s} className={s === screen ? "active" : ""} onClick={() => nav.go(s)} disabled={!project && !["Projects", "Setup", "Queue"].includes(s)}>
            {s === "Map" ? "Map & recompose" : s}
          </button>
        ))}
      </nav>
      <main className="main">
        {screen === "Projects" && <Projects />}
        {screen === "Cues" && project && <Cues initial={arg?.cue} />}
        {screen === "Style" && project && <Style />}
        {screen === "Queue" && <QueueScreen initial={arg?.job} />}
        {screen === "Audition" && project && <Audition />}
        {screen === "Map" && project && <MapScreen initial={arg?.file} />}
        {screen === "Setup" && <Setup />}
      </main>
      <AgentPanel enabled={!!health?.agent && !!project} />
      <StatusBar health={health} connected={connected} />
    </div>
  );
}

function EngineDown() {
  return (
    <div className="engine-down">
      <h1>Engine not running</h1>
      <p>Start it with <b>start.cmd</b> in Sodaudio folder; this window reconnects by itself.</p>
      <code>Sodaudio\start.cmd</code>
      <p className="muted">The engine listens on http://127.0.0.1:8770.</p>
    </div>
  );
}
