const base = () => (import.meta.env.VITE_API_BASE as string | undefined) || "";

export async function fetchCapabilities() {
  const res = await fetch(`${base()}/api/capabilities`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{
    demucs_ready: boolean;
    lalal_configured: boolean;
    ffmpeg_ok: boolean;
    models: { id: string; label: string; stems: string[]; description: string }[];
    demucs_shifts: string;
    stem_backend_order: string;
    hint: string;
  }>;
}

export async function uploadSession(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${base()}/api/session/upload`, { method: "POST", body: fd });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ job_id: string; duration_sec: number; original_name: string }>;
}

export async function analyzeSession(jobId: string) {
  const res = await fetch(`${base()}/api/session/${jobId}/analyze`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{
    job_id: string;
    duration_sec: number;
    modes: { id: string; label: string; stems: string[]; description: string }[];
    demucs_ready: boolean;
    lalal_configured: boolean;
    demucs_hint: string;
  }>;
}

/** Avvia separazione in background (risponde subito). */
export async function startSeparateJob(jobId: string, modelId: string) {
  const res = await fetch(`${base()}/api/session/${jobId}/separate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: modelId }),
  });
  if (res.status === 409) {
    throw new Error("Separazione già in corso. Attendi il completamento.");
  }
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ status: string; job_id: string }>;
}

export async function getSeparationStatus(jobId: string) {
  const res = await fetch(`${base()}/api/session/${jobId}/separation_status`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{
    job_id: string;
    status: string;
    percent: number;
    message: string;
    stems?: string[];
    used_demucs?: boolean;
    separation_engine?: string | null;
    separation_warning?: string | null;
    manifest?: string;
    error?: string;
  }>;
}

export function stemWavUrl(jobId: string, stem: string) {
  return `${base()}/api/session/${jobId}/stem/${encodeURIComponent(stem)}/wav`;
}

export async function exportZip(jobId: string, fmt: string) {
  const fd = new FormData();
  fd.append("fmt", fmt);
  const res = await fetch(`${base()}/api/session/${jobId}/export_zip`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.blob();
}

export async function exportMix(jobId: string, fmt: string, volumes: Record<string, number>) {
  const fd = new FormData();
  fd.append("fmt", fmt);
  fd.append("volumes_json", JSON.stringify(volumes));
  const res = await fetch(`${base()}/api/session/${jobId}/export_mix`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.blob();
}

export async function uploadDeveloperPair(params: {
  title: string;
  notes: string;
  mix: File;
  stemsZip?: File;
  stemFiles?: File[];
}) {
  const fd = new FormData();
  fd.append("title", params.title);
  fd.append("notes", params.notes);
  fd.append("mix", params.mix);
  if (params.stemsZip) fd.append("stems_archive", params.stemsZip);
  for (const f of params.stemFiles ?? []) {
    fd.append("stem_files", f);
  }
  const res = await fetch(`${base()}/api/developer/pair`, { method: "POST", body: fd });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ id: number; message: string }>;
}

export async function listDeveloperPairs() {
  const res = await fetch(`${base()}/api/developer/pairs`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ id: number; title: string; created_at: string; notes: string }[]>;
}
