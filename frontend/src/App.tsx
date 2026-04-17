import { useState } from "react";
import { DeveloperPanel } from "./DeveloperPanel";
import { StudioFlow } from "./StudioFlow";

type Tab = "studio" | "developer";

export default function App() {
  const [tab, setTab] = useState<Tab>("studio");

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="logo">Stem Studio</div>
        <nav className="tabs" aria-label="Sezioni principali">
          <button type="button" className={tab === "studio" ? "active" : ""} onClick={() => setTab("studio")}>
            Studio
          </button>
          <button
            type="button"
            className={tab === "developer" ? "active" : ""}
            onClick={() => setTab("developer")}
          >
            Developer
          </button>
        </nav>
      </header>

      {tab === "studio" ? (
        <p className="muted" style={{ margin: "-0.5rem 0 1rem" }}>
          Sei in <strong style={{ color: "var(--text)" }}>Studio</strong>: carica un file nella prima area qui sotto
          (il pulsante «Studio» in alto serve solo a tornare qui se vai su Developer).
        </p>
      ) : null}

      {tab === "studio" ? <StudioFlow /> : <DeveloperPanel />}
    </div>
  );
}
