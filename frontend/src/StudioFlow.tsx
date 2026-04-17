import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  analyzeSession,
  exportMix,
  exportZip,
  fetchCapabilities,
  getSeparationStatus,
  startSeparateJob,
  stemWavUrl,
  uploadSession,
} from "./api";

type Phase = "idle" | "uploaded" | "analyzed" | "separated";

const ACCEPTED_EXT = new Set([
  ".wav",
  ".mp3",
  ".m4a",
  ".aac",
  ".aif",
  ".aiff",
  ".flac",
  ".ogg",
  ".mp4",
  ".avi",
  ".mpeg",
  ".mpg",
  ".mov",
  ".mkv",
]);

function fileExtension(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function isAcceptedAudioOrVideo(file: File): boolean {
  return ACCEPTED_EXT.has(fileExtension(file.name));
}

const EXPORT_FORMATS = [
  { id: "wav24", label: "WAV 24-bit PCM" },
  { id: "flac", label: "FLAC (lossless)" },
  { id: "aiff", label: "AIFF 24-bit" },
  { id: "mp3_320", label: "MP3 320 kbps" },
  { id: "aac_256", label: "AAC / M4A 256 kbps" },
  { id: "opus", label: "Opus 256 kbps" },
] as const;

function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export function StudioFlow() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [durationSec, setDurationSec] = useState<number | null>(null);
  const [fileName, setFileName] = useState<string>("");
  const [modes, setModes] = useState<
    { id: string; label: string; stems: string[]; description: string }[]
  >([]);
  const [selectedMode, setSelectedMode] = useState<string>("mdx_extra");
  const [stems, setStems] = useState<string[]>([]);
  const [usedDemucs, setUsedDemucs] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [exportFmt, setExportFmt] = useState<string>("wav24");
  const [serverCaps, setServerCaps] = useState<Awaited<ReturnType<typeof fetchCapabilities>> | null>(null);
  const [capsErr, setCapsErr] = useState<string | null>(null);
  const [separationWarning, setSeparationWarning] = useState<string | null>(null);
  const [separating, setSeparating] = useState(false);
  const [separatePercent, setSeparatePercent] = useState(0);
  const [separateMessage, setSeparateMessage] = useState("");
  const pollCancelRef = useRef(false);

  const [gainMap, setGainMap] = useState<Record<string, number>>({});

  const audioCtxRef = useRef<AudioContext | null>(null);
  const buffersRef = useRef<Record<string, AudioBuffer>>({});
  const gainsRef = useRef<Record<string, GainNode>>({});
  const masterRef = useRef<GainNode | null>(null);
  const sourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const [playing, setPlaying] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);

  const ensureCtx = useCallback(async () => {
    if (!audioCtxRef.current) {
      audioCtxRef.current = new AudioContext();
      masterRef.current = audioCtxRef.current.createGain();
      masterRef.current.gain.value = 0.9;
      masterRef.current.connect(audioCtxRef.current.destination);
    }
    if (audioCtxRef.current.state === "suspended") {
      await audioCtxRef.current.resume();
    }
    return audioCtxRef.current;
  }, []);

  const stopPlayback = useCallback(() => {
    for (const s of sourcesRef.current) {
      try {
        s.stop();
      } catch {
        /* ignore */
      }
    }
    sourcesRef.current = [];
    setPlaying(false);
  }, []);

  const startPlayback = useCallback(async () => {
    const ctx = await ensureCtx();
    stopPlayback();
    const master = masterRef.current!;
    const when = ctx.currentTime + 0.02;
    const list: AudioBufferSourceNode[] = [];
    for (const stem of stems) {
      const buf = buffersRef.current[stem];
      if (!buf) continue;
      let g = gainsRef.current[stem];
      if (!g) {
        g = ctx.createGain();
        g.connect(master);
        gainsRef.current[stem] = g;
      }
      g.gain.value = gainMap[stem] ?? 1;
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(g);
      src.start(when, 0);
      list.push(src);
    }
    sourcesRef.current = list;
    setPlaying(true);
  }, [ensureCtx, gainMap, stems, stopPlayback]);

  const decodeStems = useCallback(
    async (id: string, names: string[]) => {
      const ctx = await ensureCtx();
      buffersRef.current = {};
      gainsRef.current = {};
      for (const stem of names) {
        const res = await fetch(stemWavUrl(id, stem));
        if (!res.ok) throw new Error(`Stem ${stem}: ${res.status}`);
        const arr = await res.arrayBuffer();
        const buf = await ctx.decodeAudioData(arr.slice(0));
        buffersRef.current[stem] = buf;
      }
    },
    [ensureCtx],
  );

  useEffect(() => {
    return () => {
      pollCancelRef.current = true;
      stopPlayback();
      void audioCtxRef.current?.close();
    };
  }, [stopPlayback]);

  useEffect(() => {
    let alive = true;
    void fetchCapabilities()
      .then((c) => {
        if (alive) {
          setServerCaps(c);
          setCapsErr(null);
        }
      })
      .catch(() => {
        if (alive) {
          setServerCaps(null);
          setCapsErr(
            "Non riesco a contattare il server. Avvia il backend (uvicorn) oppure apri l’URL giusto del sito dove è ospitato.",
          );
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  const runUpload = useCallback(async (file: File) => {
    if (!isAcceptedAudioOrVideo(file)) {
      setErr(
        `Formato non supportato (${fileExtension(file.name) || "sconosciuto"}). Usa audio o video tra: ${[...ACCEPTED_EXT].join(", ")}`,
      );
      return;
    }
    setErr(null);
    setBusy(true);
    try {
      const r = await uploadSession(file);
      setJobId(r.job_id);
      setDurationSec(r.duration_sec);
      setFileName(r.original_name);
      setPhase("uploaded");
      setStems([]);
      setUsedDemucs(null);
      setSeparationWarning(null);
      setSeparatePercent(0);
      setSeparateMessage("");
    } catch (ex) {
      const msg = String(ex);
      if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
        setErr(
          "Connessione al server non riuscita. Avvia il backend nel Terminale (uvicorn sulla porta 8765) e ricarica la pagina.",
        );
      } else {
        setErr(msg);
      }
    } finally {
      setBusy(false);
    }
  }, []);

  const onPickFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (f) void runUpload(f);
  };

  const onDropFiles = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    const f = e.dataTransfer.files?.[0];
    if (f) void runUpload(f);
  };

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "copy";
  };

  const onDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(true);
  };

  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.currentTarget === e.target) setDragActive(false);
  };

  const runAnalyze = async () => {
    if (!jobId) return;
    setErr(null);
    setBusy(true);
    try {
      const a = await analyzeSession(jobId);
      setModes(a.modes);
      setDurationSec(a.duration_sec);
      setPhase("analyzed");
      void fetchCapabilities()
        .then(setServerCaps)
        .catch(() => undefined);
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  };

  const runSeparate = async () => {
    if (!jobId) return;
    setErr(null);
    setBusy(true);
    setSeparating(true);
    setSeparatePercent(0);
    setSeparateMessage("Avvio…");
    stopPlayback();
    try {
      await startSeparateJob(jobId, selectedMode);
      const deadline = Date.now() + 8 * 60 * 60 * 1000;
      while (!pollCancelRef.current) {
        if (Date.now() > deadline) {
          throw new Error("Timeout: la separazione sta impiegando più di 8 ore.");
        }
        const st = await getSeparationStatus(jobId);
        setSeparatePercent(st.percent);
        setSeparateMessage(st.message || (st.status === "processing" ? "Separazione in corso…" : ""));
        if (st.status === "separated" && st.stems?.length) {
          setStems(st.stems);
          const init: Record<string, number> = {};
          for (const n of st.stems) init[n] = 1;
          setGainMap(init);
          setUsedDemucs(st.used_demucs ?? null);
          setSeparationWarning(st.separation_warning ?? null);
          setPhase("separated");
          setSeparatePercent(100);
          setSeparateMessage("Completato.");
          await decodeStems(jobId, st.stems);
          break;
        }
        if (st.status === "error") {
          throw new Error(st.error || st.message || "Separazione fallita");
        }
        await new Promise((r) => setTimeout(r, 450));
      }
      if (pollCancelRef.current) {
        return;
      }
    } catch (ex) {
      setErr(String(ex));
      setSeparatePercent(0);
    } finally {
      setBusy(false);
      setSeparating(false);
    }
  };

  const setGain = (stem: string, v: number) => {
    setGainMap((m) => ({ ...m, [stem]: v }));
    const gn = gainsRef.current[stem];
    if (gn && audioCtxRef.current) {
      gn.gain.setTargetAtTime(v, audioCtxRef.current.currentTime, 0.02);
    }
  };

  const volumesForExport = useMemo(() => ({ ...gainMap }), [gainMap]);

  const onExportZip = async () => {
    if (!jobId) return;
    setBusy(true);
    setErr(null);
    try {
      const blob = await exportZip(jobId, exportFmt);
      downloadBlob(blob, `stems_${jobId.slice(0, 8)}.zip`);
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  };

  const onExportMix = async () => {
    if (!jobId) return;
    setBusy(true);
    setErr(null);
    try {
      const blob = await exportMix(jobId, exportFmt, volumesForExport);
      const ext =
        exportFmt === "wav24"
          ? "wav"
          : exportFmt === "flac"
            ? "flac"
            : exportFmt === "aiff"
              ? "aif"
              : exportFmt === "mp3_320"
                ? "mp3"
                : exportFmt === "aac_256"
                  ? "m4a"
                  : "opus";
      downloadBlob(blob, `mix_${jobId.slice(0, 8)}.${ext}`);
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {capsErr ? <div className="alert alert-warning">{capsErr}</div> : null}
      {serverCaps && !serverCaps.demucs_ready ? (
        <div className="alert alert-warning">
          <strong>Modalità qualità ridotta (demo).</strong> Sul server non risulta installato Demucs con PyTorch: le
          “tracce” sono copie del brano intero, quindi <strong>i fader non possono isolare davvero voce o batteria</strong>
          . Per la qualità professionale installa sul computer/server i pacchetti in{" "}
          <code style={{ fontSize: "0.85em" }}>backend/requirements-ml.txt</code> oppure usa il deploy con{" "}
          <code style={{ fontSize: "0.85em" }}>Dockerfile</code> (Demucs incluso).
        </div>
      ) : null}
      {serverCaps && serverCaps.demucs_ready ? (
        <div className="alert alert-info">
          Demucs attivo: preset qualità alta (<strong>shifts = {serverCaps.demucs_shifts}</strong>, più alto = più lento
          e di solito migliore). In elenco, <strong>mdx_extra</strong> è il modello più accurato; <strong>htdemucs_ft</strong>{" "}
          è un ottimo compromesso. Per andare più veloce sul server imposta la variabile{" "}
          <code style={{ fontSize: "0.85em" }}>DEMUCS_SHIFTS=2</code> (o <code>0</code> senza shifts).
        </div>
      ) : null}

      <section className="panel">
        <h2>1. Carica file</h2>
        <p className="muted">
          Audio: WAV, MP3, AIFF, M4A · Video: MP4, AVI, MPEG (l’audio viene estratto automaticamente).
        </p>
        <input
          ref={fileInputRef}
          type="file"
          className="visually-hidden"
          id="studio-file-input"
          accept=".wav,.mp3,.m4a,.aac,.aif,.aiff,.flac,.ogg,.mp4,.avi,.mpeg,.mpg,.mov,.mkv"
          onChange={onPickFile}
          disabled={busy}
        />
        <div
          className={`upload-dropzone${dragActive ? " drag-active" : ""}`}
          onDrop={onDropFiles}
          onDragOver={onDragOver}
          onDragEnter={onDragEnter}
          onDragLeave={onDragLeave}
        >
          <p>
            <strong>Trascina qui</strong> un file audio o video, oppure premi il pulsante per sceglierlo dal computer.
          </p>
          <div className="upload-actions">
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => fileInputRef.current?.click()}
            >
              Scegli file dal computer…
            </button>
          </div>
        </div>
        {fileName ? (
          <p className="muted" style={{ marginTop: "0.65rem" }}>
            {fileName}
            {durationSec != null ? ` · ${durationSec.toFixed(1)} s` : ""}
          </p>
        ) : null}
      </section>

      {phase !== "idle" ? (
        <section className="panel">
          <h2>2. Analisi</h2>
          <p className="muted">Calcola durata e modalità di separazione disponibili.</p>
          <button type="button" className="secondary" onClick={runAnalyze} disabled={busy}>
            Analizza
          </button>
        </section>
      ) : null}

      {phase === "analyzed" || phase === "separated" ? (
        <section className="panel">
          <h2>3. Scegli modello e separa</h2>
          <p className="muted">
            Ogni modalità indica in quante tracce può dividere il brano (tipicamente 4 stem). Scegli quella che preferisci
            e avvia la separazione.
          </p>
          <div className="mode-list">
            {modes.map((m) => (
              <div
                key={m.id}
                className={`mode-card${selectedMode === m.id ? " selected" : ""}`}
                onClick={() => setSelectedMode(m.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(ev) => {
                  if (ev.key === "Enter" || ev.key === " ") setSelectedMode(m.id);
                }}
              >
                <div>
                  <strong>{m.label}</strong>
                  <div className="muted">{m.description}</div>
                  <div className="muted">
                    {m.stems.length} tracce: {m.stems.join(", ")}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: "0.75rem" }}>
            <button type="button" className="primary" onClick={() => void runSeparate()} disabled={busy}>
              {separating ? "Separazione in corso…" : "Avvia separazione"}
            </button>
          </div>
          {(separating || separatePercent > 0) && (phase === "analyzed" || phase === "separated") ? (
            <div className="progress-wrap">
              <div className="progress-label">
                <span>Avanzamento separazione</span>
                <span>{Math.round(Math.min(100, Math.max(0, separatePercent)))}%</span>
              </div>
              <div className="progress-track" aria-valuenow={Math.round(separatePercent)} aria-valuemin={0} aria-valuemax={100} role="progressbar">
                <div
                  className="progress-fill"
                  style={{ width: `${Math.min(100, Math.max(0, separatePercent))}%` }}
                />
              </div>
              {separateMessage ? <div className="progress-detail">{separateMessage}</div> : null}
              <p className="muted" style={{ marginTop: "0.5rem", fontSize: "0.82rem" }}>
                La percentuale arriva dall’output di Demucs quando disponibile; all’inizio può restare bassa per diversi
                minuti mentre il modello lavora senza aggiornare la barra.
              </p>
            </div>
          ) : null}
          {usedDemucs === false ? (
            <p className="muted" style={{ marginTop: "0.65rem" }}>
              Separazione in modalità demo: vedi l’avviso giallo in alto per installare Demucs sul server.
            </p>
          ) : null}
        </section>
      ) : null}

      {phase === "separated" && stems.length ? (
        <section className="panel">
          <h2>4. Mixer</h2>
          {separationWarning ? (
            <div className="alert alert-warning" style={{ marginBottom: "0.85rem" }}>
              {separationWarning}
            </div>
          ) : null}
          <p className="muted">Regola i volumi come in una DAW; poi esporta.</p>
          <div className="transport">
            <button type="button" className="secondary" onClick={() => (playing ? stopPlayback() : void startPlayback())}>
              {playing ? "Stop" : "Play"}
            </button>
          </div>
          <div className="mixer-grid" key={`${jobId}-${stems.join("|")}`}>
            {stems.map((stem) => (
              <div className="channel" key={stem}>
                <header>
                  <span>{stem}</span>
                  <span className="muted">{(gainMap[stem] ?? 1).toFixed(2)}</span>
                </header>
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.01}
                  value={gainMap[stem] ?? 1}
                  onChange={(ev) => setGain(stem, Number((ev.target as HTMLInputElement).value))}
                />
              </div>
            ))}
          </div>
          <div className="export-row" style={{ marginTop: "1rem" }}>
            <span className="muted">Formato export</span>
            <select value={exportFmt} onChange={(e) => setExportFmt(e.target.value)}>
              {EXPORT_FORMATS.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label}
                </option>
              ))}
            </select>
            <button type="button" className="secondary" onClick={onExportZip} disabled={busy}>
              ZIP stem
            </button>
            <button type="button" className="primary" onClick={onExportMix} disabled={busy}>
              Export mix
            </button>
          </div>
        </section>
      ) : null}

      {err ? <p className="error">{err}</p> : null}
    </>
  );
}
