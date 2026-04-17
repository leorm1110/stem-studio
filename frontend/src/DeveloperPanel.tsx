import { useEffect, useState } from "react";
import { listDeveloperPairs, uploadDeveloperPair } from "./api";

export function DeveloperPanel() {
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [mix, setMix] = useState<File | null>(null);
  const [zip, setZip] = useState<File | null>(null);
  const [stems, setStems] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [rows, setRows] = useState<{ id: number; title: string; created_at: string; notes: string }[]>([]);

  const refresh = async () => {
    try {
      setRows(await listDeveloperPairs());
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const submit = async () => {
    if (!mix) {
      setErr("Carica il mix completo.");
      return;
    }
    if (!zip && stems.length === 0) {
      setErr("Carica uno ZIP con gli stem oppure seleziona più file stem.");
      return;
    }
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const r = await uploadDeveloperPair({
        title,
        notes,
        mix,
        stemsZip: zip ?? undefined,
        stemFiles: stems.length ? stems : undefined,
      });
      setMsg(r.message);
      setTitle("");
      setNotes("");
      setMix(null);
      setZip(null);
      setStems([]);
      await refresh();
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="panel">
        <h2>Funzione Developer</h2>
        <p className="muted">
          Carica il brano mixato e la stessa sessione con stem già separati (ZIP o più file). I dati restano sul server
          per una futura pipeline di training: questa UI non esegue ancora fine-tuning automatico.
        </p>
        <div style={{ display: "grid", gap: "0.65rem", maxWidth: 520 }}>
          <label className="muted">
            Titolo
            <input
              style={{ width: "100%", marginTop: 4, padding: "0.45rem", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)" }}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Es. Rock session 042"
            />
          </label>
          <label className="muted">
            Note
            <textarea
              style={{ width: "100%", marginTop: 4, padding: "0.45rem", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)", minHeight: 72 }}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Annotazioni per il dataset"
            />
          </label>
          <label className="muted">
            Mix intero
            <input type="file" onChange={(e) => setMix(e.target.files?.[0] ?? null)} />
          </label>
          <label className="muted">
            Stem (ZIP)
            <input type="file" accept=".zip,application/zip" onChange={(e) => setZip(e.target.files?.[0] ?? null)} />
          </label>
          <label className="muted">
            Oppure stem multipli
            <input type="file" multiple onChange={(e) => setStems(e.target.files ? Array.from(e.target.files) : [])} />
          </label>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy}>
            Invia al dataset
          </button>
        </div>
        {msg ? <p className="muted" style={{ marginTop: "0.75rem" }}>{msg}</p> : null}
        {err ? <p className="error" style={{ marginTop: "0.75rem" }}>{err}</p> : null}
      </section>

      <section className="panel">
        <h2>Ultime coppie registrate</h2>
        {rows.length === 0 ? <p className="muted">Nessun caricamento ancora.</p> : null}
        <ul style={{ paddingLeft: "1.1rem", margin: 0 }}>
          {rows.map((r) => (
            <li key={r.id} style={{ marginBottom: "0.35rem" }}>
              <strong>{r.title}</strong>{" "}
              <span className="muted">
                #{r.id} · {new Date(r.created_at).toLocaleString()}
              </span>
              {r.notes ? <div className="muted">{r.notes}</div> : null}
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
