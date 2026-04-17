from __future__ import annotations

import io
import json
import os
import shutil
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.db import DEV_DIR, STEM_DIR, DeveloperPair, TrainingJob, get_db, init_db
from app.ffmpeg_utils import (
    export_stem,
    extract_audio_from_video,
    mix_wavs_with_volumes,
    normalize_to_wav_48k_stereo,
    probe_duration_seconds,
)
from app.lalal_client import lalal_configured
from app.separation import (
    DEMUCS_MODELS,
    demucs_cli_ok,
    read_separation_progress,
    separate,
    write_manifest,
    write_separation_progress,
)

app = FastAPI(title="Stem Studio API", version="0.1.0")

_APP_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _APP_DIR.parent
_REPO_ROOT = _BACKEND_DIR.parent


def _static_dir() -> Path:
    env = os.environ.get("STEM_STATIC_DIR", "").strip()
    if env:
        return Path(env).resolve()
    return (_REPO_ROOT / "frontend" / "dist").resolve()


def _safe_static_file(full_path: str) -> Path | None:
    base = _static_dir()
    if not base.is_dir():
        return None
    if full_path.startswith("api/"):
        return None
    candidate = (base / full_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


VIDEO_EXT = {".mp4", ".avi", ".mpeg", ".mpg", ".mov", ".mkv"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".aif", ".aiff"}


def _job_dir(job_id: str) -> Path:
    return STEM_DIR / job_id


def _read_job_meta(job_id: str) -> dict:
    p = _job_dir(job_id) / "job.json"
    if not p.exists():
        raise HTTPException(404, "Sessione non trovata")
    return json.loads(p.read_text(encoding="utf-8"))


def _write_job_meta(job_id: str, data: dict) -> None:
    d = _job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    (_job_dir(job_id) / "job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/capabilities")
def capabilities() -> dict:
    return {
        "demucs_ready": demucs_cli_ok(),
        "lalal_configured": lalal_configured(),
        "ffmpeg_ok": shutil.which("ffmpeg") is not None,
        "models": DEMUCS_MODELS,
        "demucs_shifts": os.environ.get("DEMUCS_SHIFTS", "10"),
        "stem_backend_order": os.environ.get("STEM_BACKEND_ORDER", "demucs,lalal,demo"),
        "hint": "Un solo sito: avvia questo server con la cartella frontend compilata in STEM_STATIC_DIR (vedi Dockerfile). "
        "Per separazione in cloud senza GPU: imposta LALAL_LICENSE_KEY (LALAL.AI API v1).",
    }


@app.post("/api/session/upload")
async def upload_session(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in VIDEO_EXT | AUDIO_EXT:
        raise HTTPException(400, f"Estensione non supportata: {suffix}")

    job_id = str(uuid.uuid4())
    job_root = _job_dir(job_id)
    job_root.mkdir(parents=True, exist_ok=True)
    raw_path = job_root / f"original{suffix}"
    content = await file.read()
    raw_path.write_bytes(content)

    work_wav = job_root / "working.wav"
    try:
        if suffix in VIDEO_EXT:
            extract_audio_from_video(raw_path, work_wav)
        else:
            normalize_to_wav_48k_stereo(raw_path, work_wav)
        duration = probe_duration_seconds(work_wav)
    except Exception as e:
        shutil.rmtree(job_root, ignore_errors=True)
        raise HTTPException(500, f"Elaborazione fallita: {e}") from e

    meta = {
        "job_id": job_id,
        "original_name": file.filename,
        "duration_sec": duration,
        "status": "ready_for_analyze",
    }
    _write_job_meta(job_id, meta)
    return {"job_id": job_id, "duration_sec": duration, "original_name": file.filename}


@app.get("/api/session/{job_id}/analyze")
def analyze_session(job_id: str) -> dict:
    _ = _read_job_meta(job_id)
    work_wav = _job_dir(job_id) / "working.wav"
    if not work_wav.exists():
        raise HTTPException(404, "Audio di lavoro mancante")
    duration = probe_duration_seconds(work_wav)
    return {
        "job_id": job_id,
        "duration_sec": duration,
        "modes": DEMUCS_MODELS,
        "demucs_ready": demucs_cli_ok(),
        "lalal_configured": lalal_configured(),
        "demucs_hint": "Se demucs_ready è false e non c’è LALAL_LICENSE_KEY, l’app userà stem demo (copie del mix). "
        "Per qualità reale: Demucs locale/Docker oppure chiave API LALAL.AI.",
    }


class SeparateRequest(BaseModel):
    model_id: str = Field(..., description="ID modello Demucs, es. htdemucs_ft")


def _separation_worker(job_id: str, model_id: str) -> None:
    job_root = _job_dir(job_id)
    wav = job_root / "working.wav"
    try:
        stems, used_demucs, sep_warning, engine = separate(wav, job_root, model_id, prefer_demucs=True)
        manifest = write_manifest(job_root, stems)
        meta = _read_job_meta(job_id)
        meta["status"] = "separated"
        meta["stems"] = {k: str(Path(v).name) for k, v in stems.items()}
        meta["manifest"] = str(manifest.relative_to(job_root))
        meta["used_demucs"] = used_demucs
        meta["separation_engine"] = engine
        meta["separation_warning"] = sep_warning
        _write_job_meta(job_id, meta)
    except Exception as e:
        try:
            meta = _read_job_meta(job_id)
        except Exception:
            return
        meta["status"] = "error"
        meta["error"] = str(e)
        _write_job_meta(job_id, meta)
        write_separation_progress(job_root, 0, str(e)[:500])


@app.post("/api/session/{job_id}/separate")
def run_separate(job_id: str, body: SeparateRequest) -> dict:
    model_id = body.model_id
    meta = _read_job_meta(job_id)
    if meta.get("status") == "processing":
        raise HTTPException(409, "Separazione già in corso")
    wav = _job_dir(job_id) / "working.wav"
    if not wav.exists():
        raise HTTPException(404, "working.wav mancante")

    meta["status"] = "processing"
    meta["model_id"] = model_id
    meta.pop("error", None)
    meta.pop("separation_warning", None)
    meta.pop("separation_engine", None)
    _write_job_meta(job_id, meta)

    threading.Thread(target=_separation_worker, args=(job_id, model_id), daemon=True).start()
    return {"status": "processing", "job_id": job_id}


@app.get("/api/session/{job_id}/separation_status")
def separation_status(job_id: str) -> dict[str, Any]:
    """Stato separazione + percentuale (polling dal frontend)."""
    meta = _read_job_meta(job_id)
    status = meta.get("status", "unknown")
    job_root = _job_dir(job_id)
    prog = read_separation_progress(job_root)
    out: dict[str, Any] = {
        "job_id": job_id,
        "status": status,
        "percent": int(prog.get("percent", 0) or 0),
        "message": str(prog.get("message", "") or ""),
    }
    if status == "separated":
        stems_info = meta.get("stems")
        if isinstance(stems_info, dict):
            out["stems"] = list(stems_info.keys())
        elif isinstance(stems_info, list):
            out["stems"] = stems_info
        else:
            out["stems"] = []
        out["used_demucs"] = meta.get("used_demucs")
        out["separation_engine"] = meta.get("separation_engine")
        out["separation_warning"] = meta.get("separation_warning")
        out["manifest"] = meta.get("manifest")
        out["percent"] = 100
        out["message"] = "Completato."
    elif status == "error":
        out["error"] = meta.get("error", "")
        out["percent"] = min(out["percent"], 5) if out["percent"] else 0
    return out


@app.get("/api/session/{job_id}/stem/{stem_name}/wav")
def get_stem_wav(job_id: str, stem_name: str) -> FileResponse:
    path = _job_dir(job_id) / "stems" / f"{stem_name}.wav"
    if not path.exists():
        raise HTTPException(404, "Stem non trovato")
    return FileResponse(path, media_type="audio/wav", filename=f"{stem_name}.wav")


@app.post("/api/session/{job_id}/export_zip")
def export_zip(job_id: str, fmt: str = Form("wav24")) -> StreamingResponse:
    meta = _read_job_meta(job_id)
    if meta.get("status") != "separated":
        raise HTTPException(400, "Separazione non completata")
    stems_dir = _job_dir(job_id) / "stems"
    ext_map = {
        "wav24": ".wav",
        "flac": ".flac",
        "aiff": ".aif",
        "mp3_320": ".mp3",
        "aac_256": ".m4a",
        "opus": ".opus",
    }
    if fmt not in ext_map:
        raise HTTPException(400, "Formato non supportato")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for wav in sorted(stems_dir.glob("*.wav")):
            stem = wav.stem
            tmp = _job_dir(job_id) / "export_tmp" / f"{stem}{ext_map[fmt]}"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            export_stem(wav, tmp, fmt)
            zf.write(tmp, arcname=f"{stem}{ext_map[fmt]}")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="stems_{job_id[:8]}.zip"'},
    )


@app.post("/api/session/{job_id}/export_mix")
def export_mix(
    job_id: str,
    fmt: str = Form("wav24"),
    volumes_json: str = Form(...),
) -> FileResponse:
    meta = _read_job_meta(job_id)
    if meta.get("status") != "separated":
        raise HTTPException(400, "Separazione non completata")
    try:
        volumes: dict[str, float] = json.loads(volumes_json)
    except json.JSONDecodeError as e:
        raise HTTPException(400, "volumes_json non valido") from e

    stems_dir = _job_dir(job_id) / "stems"
    pairs: list[tuple[Path, float]] = []
    for name, gain in volumes.items():
        p = stems_dir / f"{name}.wav"
        if p.exists():
            pairs.append((p, float(gain)))
    if not pairs:
        raise HTTPException(400, "Nessuno stem valido")

    out_dir = _job_dir(job_id) / "export_tmp"
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = {
        "wav24": ".wav",
        "flac": ".flac",
        "aiff": ".aif",
        "mp3_320": ".mp3",
        "aac_256": ".m4a",
        "opus": ".opus",
    }.get(fmt)
    if not ext:
        raise HTTPException(400, "Formato non supportato")
    out_path = out_dir / f"mix_{fmt}{ext}"
    mix_wavs_with_volumes(pairs, out_path, fmt)
    return FileResponse(out_path, filename=f"mix_{job_id[:8]}{ext}")


@app.post("/api/developer/pair")
async def developer_pair(
    db: Annotated[Session, Depends(get_db)],
    title: str = Form(""),
    notes: str = Form(""),
    mix: UploadFile = File(...),
    stems_archive: UploadFile | None = File(None),
    stem_files: list[UploadFile] | None = File(None),
) -> dict:
    """
    Carica mix + archivio stem (zip) oppure più file stem.
    Crea record DB e directory per futuro training.
    """
    pair_id = str(uuid.uuid4())
    root = DEV_DIR / pair_id
    root.mkdir(parents=True, exist_ok=True)
    mix_suffix = Path(mix.filename or "mix.wav").suffix.lower()
    mix_path = root / f"mix{mix_suffix}"
    mix_path.write_bytes(await mix.read())

    manifest: dict[str, str] = {}
    stems_folder = root / "stems"
    stems_folder.mkdir(exist_ok=True)

    if stems_archive and stems_archive.filename:
        zdata = await stems_archive.read()
        zpath = root / "stems_upload.zip"
        zpath.write_bytes(zdata)
        with zipfile.ZipFile(io.BytesIO(zdata)) as zf:
            for name in zf.namelist():
                if name.endswith("/") or name.startswith("__MACOSX"):
                    continue
                dest = stems_folder / Path(name).name
                dest.write_bytes(zf.read(name))
                manifest[dest.stem] = str(dest.relative_to(root))

    if stem_files:
        for uf in stem_files:
            if not uf.filename:
                continue
            dest = stems_folder / Path(uf.filename).name
            dest.write_bytes(await uf.read())
            manifest[dest.stem] = str(dest.relative_to(root))

    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    row = DeveloperPair(
        title=title or pair_id,
        mix_path=str(mix_path.relative_to(DEV_DIR)),
        stems_manifest_path=str(manifest_path.relative_to(DEV_DIR)),
        notes=notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    job = TrainingJob(pair_id=row.id, status="queued", message="In attesa di pipeline ML")
    db.add(job)
    db.commit()

    return {
        "id": row.id,
        "message": "Coppia salvata. Il training automatico non è ancora attivo: serve pipeline GPU.",
    }


@app.get("/api/developer/pairs")
def list_pairs(db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    rows = db.query(DeveloperPair).order_by(DeveloperPair.id.desc()).limit(200).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "created_at": r.created_at.isoformat(),
            "notes": r.notes,
        }
        for r in rows
    ]


@app.get("/", response_model=None)
def spa_root():
    idx = _static_dir() / "index.html"
    if idx.is_file():
        return FileResponse(idx)
    return {
        "service": "Stem Studio API",
        "docs": "/docs",
        "health": "/api/health",
        "note": "Compila il frontend (npm run build) o imposta STEM_STATIC_DIR verso la cartella dist.",
    }


@app.get("/{full_path:path}", response_model=None)
def spa_assets(full_path: str):
    static = _static_dir()
    if not static.is_dir():
        raise HTTPException(404, "Frontend non trovato")
    if full_path.startswith("api"):
        raise HTTPException(404)
    hit = _safe_static_file(full_path)
    if hit:
        return FileResponse(hit)
    idx = static / "index.html"
    if idx.is_file():
        return FileResponse(idx)
    raise HTTPException(404)
