"""
Client LALAL.AI API v1 (multistem): upload → split → poll → download.
Documentazione: https://www.lalal.ai/api/v1/docs/

Variabili d'ambiente:
  LALAL_LICENSE_KEY   — obbligatoria per usare il cloud
  LALAL_API_BASE      — opzionale, default https://www.lalal.ai/api/v1/
  LALAL_SPLITTER      — opzionale: perseus, orion, phoenix, andromeda, lyra
  LALAL_EXTRACTION_LEVEL — deep_extraction (default) o clear_cut
  LALAL_DELETE_AFTER  — se true/1, cancella il source su LALAL dopo il download
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests

from app.ffmpeg_utils import mix_wavs_with_volumes, normalize_to_wav_48k_stereo

logger = logging.getLogger(__name__)


def _api_base() -> str:
    raw = os.environ.get("LALAL_API_BASE", "https://www.lalal.ai/api/v1/").strip()
    return raw if raw.endswith("/") else raw + "/"


def lalal_configured() -> bool:
    return bool(os.environ.get("LALAL_LICENSE_KEY", "").strip())


def _content_disposition(filename: str) -> str:
    try:
        filename.encode("ascii")
        return f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        q = urllib.parse.quote(filename, safe="")
        return f"attachment; filename*=utf-8''{q}"


def _write_progress(job_dir: Path, pct: int, msg: str) -> None:
    from app.separation import write_separation_progress

    write_separation_progress(job_dir, pct, msg)


def _license() -> str:
    k = os.environ.get("LALAL_LICENSE_KEY", "").strip()
    if not k:
        raise RuntimeError("LALAL_LICENSE_KEY non impostata.")
    return k


def _upload(wav_path: Path, job_dir: Path) -> str:
    url = _api_base() + "upload/"
    headers = {
        "Content-Disposition": _content_disposition(wav_path.name),
        "X-License-Key": _license(),
    }
    _write_progress(job_dir, 6, "Upload su LALAL.AI…")
    with open(wav_path, "rb") as f:
        r = requests.post(url, data=f, headers=headers, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"LALAL upload HTTP {r.status_code}: {r.text[:500]}")
    data = r.json()
    sid = data.get("id")
    if not sid:
        raise RuntimeError(f"LALAL upload: risposta senza id: {data}")
    return str(sid)


def _stem_list_for_model(model_id: str) -> list[str]:
    if model_id == "htdemucs_6s":
        return ["vocals", "drum", "bass", "piano", "electric_guitar", "acoustic_guitar"]
    return ["vocals", "drum", "bass"]


def _start_multistem(source_id: str, stem_list: list[str], job_dir: Path) -> str:
    url = _api_base() + "split/multistem/"
    presets: dict[str, Any] = {
        "stem_list": stem_list,
        "extraction_level": os.environ.get("LALAL_EXTRACTION_LEVEL", "deep_extraction").strip()
        or "deep_extraction",
    }
    sp = os.environ.get("LALAL_SPLITTER", "").strip().lower()
    if sp in ("andromeda", "perseus", "orion", "phoenix", "lyra"):
        presets["splitter"] = sp
    body = {"source_id": source_id, "presets": presets}
    headers = {"X-License-Key": _license(), "Content-Type": "application/json"}
    _write_progress(job_dir, 12, "Avvio separazione LALAL.AI (multistem)…")
    r = requests.post(url, json=body, headers=headers, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"LALAL split HTTP {r.status_code}: {r.text[:500]}")
    data = r.json()
    tid = data.get("task_id")
    if not tid:
        raise RuntimeError(f"LALAL split: senza task_id: {data}")
    return str(tid)


def _poll_until_done(task_id: str, job_dir: Path) -> dict[str, Any]:
    url = _api_base() + "check/"
    headers = {"X-License-Key": _license(), "Content-Type": "application/json"}
    body = {"task_ids": [task_id]}
    deadline = time.monotonic() + float(os.environ.get("LALAL_POLL_TIMEOUT_SEC", "3600"))
    last_shown = -1
    while time.monotonic() < deadline:
        r = requests.post(url, json=body, headers=headers, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"LALAL check HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        block = (data.get("result") or {}).get(task_id)
        if not block:
            raise RuntimeError(f"LALAL check: task {task_id} assente nella risposta.")
        st = block.get("status")
        if st == "success":
            result = block.get("result")
            if not isinstance(result, dict) or "tracks" not in result:
                raise RuntimeError(f"LALAL: risultato inatteso: {block}")
            return result
        if st == "error":
            err = block.get("error")
            if isinstance(err, dict):
                msg = str(err.get("detail") or err)
            else:
                msg = str(err or "errore sconosciuto")
            raise RuntimeError(f"LALAL: {msg}")
        if st == "cancelled":
            raise RuntimeError("LALAL: task annullato.")
        if st == "server_error":
            raise RuntimeError(f"LALAL server_error: {block.get('error', block)}")
        if st == "progress":
            p = int(block.get("progress") or 0)
            if p != last_shown:
                last_shown = p
                mapped = 15 + int(min(100, p) * 0.72)
                _write_progress(job_dir, mapped, f"LALAL.AI in elaborazione… {p}%")
        else:
            _write_progress(job_dir, 14, f"LALAL.AI stato: {st}")
        time.sleep(3)
    raise RuntimeError("LALAL: timeout in attesa del completamento.")


def _download_bytes(url: str) -> bytes:
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        chunks: list[bytes] = []
        for c in r.iter_content(65536):
            if c:
                chunks.append(c)
    return b"".join(chunks)


def _maybe_delete_source(source_id: str) -> None:
    raw = os.environ.get("LALAL_DELETE_AFTER", "").strip().lower()
    if raw not in ("1", "true", "yes", "on"):
        return
    url = _api_base() + "delete/"
    headers = {"X-License-Key": _license(), "Content-Type": "application/json"}
    try:
        requests.post(url, json={"source_id": source_id}, headers=headers, timeout=60)
    except Exception as exc:
        logger.debug("LALAL delete opzionale fallito: %s", exc)


def run_lalal_separation(
    wav_in: Path,
    job_dir: Path,
    model_id: str,
    expected_stems: list[str],
) -> dict[str, Path]:
    """
    Scarica gli stem da LALAL, normalizza a WAV 48k stereo in job_dir/stems/.
    `expected_stems` è l'elenco nomi Demucs (drums, bass, …).
    """
    stems_dir = job_dir / "stems"
    if stems_dir.exists():
        shutil.rmtree(stems_dir, ignore_errors=True)
    stems_dir.mkdir(parents=True, exist_ok=True)
    dl_root = job_dir / "lalal_tmp"
    if dl_root.exists():
        shutil.rmtree(dl_root, ignore_errors=True)
    dl_root.mkdir(parents=True, exist_ok=True)

    stem_list = _stem_list_for_model(model_id)
    source_id = _upload(wav_in, job_dir)
    try:
        task_id = _start_multistem(source_id, stem_list, job_dir)
        split_result = _poll_until_done(task_id, job_dir)
        tracks: list[dict[str, Any]] = split_result.get("tracks") or []
        by_label: dict[str, str] = {}
        for t in tracks:
            lab = str(t.get("label") or "")
            u = str(t.get("url") or "")
            if lab and u:
                by_label[lab] = u

        def fetch_norm(label: str, dest_wav: Path) -> None:
            u = by_label.get(label)
            if not u:
                raise RuntimeError(f"LALAL: manca il track «{label}» nella risposta.")
            raw_path = dl_root / f"{label}_dl.bin"
            raw_path.write_bytes(_download_bytes(u))
            normalize_to_wav_48k_stereo(raw_path, dest_wav)

        is_six = model_id == "htdemucs_6s"
        if not is_six:
            need = {"vocals", "drums", "bass", "other"}
            if need != set(expected_stems):
                logger.warning("LALAL 4-stem: attesi %s, modello ha %s", need, expected_stems)
            fetch_norm("vocals", stems_dir / "vocals.wav")
            fetch_norm("drum", stems_dir / "drums.wav")
            fetch_norm("bass", stems_dir / "bass.wav")
            fetch_norm("no_multistem", stems_dir / "other.wav")
            out = {
                "vocals": stems_dir / "vocals.wav",
                "drums": stems_dir / "drums.wav",
                "bass": stems_dir / "bass.wav",
                "other": stems_dir / "other.wav",
            }
        else:
            fetch_norm("vocals", stems_dir / "vocals.wav")
            fetch_norm("drum", stems_dir / "drums.wav")
            fetch_norm("bass", stems_dir / "bass.wav")
            fetch_norm("piano", stems_dir / "piano.wav")
            elec = stems_dir / "_electric.wav"
            acou = stems_dir / "_acoustic.wav"
            fetch_norm("electric_guitar", elec)
            fetch_norm("acoustic_guitar", acou)
            mix_wavs_with_volumes([(elec, 0.5), (acou, 0.5)], stems_dir / "guitar.wav", "wav24")
            fetch_norm("no_multistem", stems_dir / "other.wav")
            try:
                elec.unlink(missing_ok=True)
                acou.unlink(missing_ok=True)
            except OSError:
                pass
            out = {
                "vocals": stems_dir / "vocals.wav",
                "drums": stems_dir / "drums.wav",
                "bass": stems_dir / "bass.wav",
                "other": stems_dir / "other.wav",
                "guitar": stems_dir / "guitar.wav",
                "piano": stems_dir / "piano.wav",
            }
            if set(expected_stems) != set(out.keys()):
                raise RuntimeError("Output LALAL 6-stem incompleto.")

        _write_progress(job_dir, 96, "Normalizzazione stem…")
        return out
    finally:
        _maybe_delete_source(source_id)
