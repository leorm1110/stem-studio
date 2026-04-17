from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEMUCS_MODELS: list[dict[str, Any]] = [
    {
        "id": "mdx_extra",
        "label": "4 stem — massima qualità (lenta)",
        "stems": ["drums", "bass", "other", "vocals"],
        "description": "MDX con più dati di training: spesso il miglior risultato su mix complessi; tempi e RAM più alti.",
    },
    {
        "id": "htdemucs_ft",
        "label": "4 stem — ottima qualità (bilanciata)",
        "stems": ["drums", "bass", "other", "vocals"],
        "description": "Hybrid Transformer fine-tuned: eccellente su pop/rock; un po’ più veloce di MDX extra.",
    },
    {
        "id": "htdemucs_6s",
        "label": "6 stem — chitarra e piano separati",
        "stems": ["drums", "bass", "other", "vocals", "guitar", "piano"],
        "description": "Aggiunge chitarra e piano; il piano può essere meno stabile su alcuni brani.",
    },
    {
        "id": "htdemucs",
        "label": "4 stem — più veloce",
        "stems": ["drums", "bass", "other", "vocals"],
        "description": "Stessa famiglia di htdemucs_ft ma più leggero e rapido, qualità leggermente inferiore.",
    },
]

_PROGRESS_NAME = "separation_progress.json"


def write_separation_progress(job_dir: Path, percent: int, message: str = "") -> None:
    pct = max(0, min(100, int(percent)))
    payload = {"percent": pct, "message": (message or "")[:500]}
    p = job_dir / _PROGRESS_NAME
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_separation_progress(job_dir: Path) -> dict[str, Any]:
    p = job_dir / _PROGRESS_NAME
    if not p.exists():
        return {"percent": 0, "message": ""}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"percent": 0, "message": ""}


def demucs_cli_ok() -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "demucs", "-h"],
            capture_output=True,
            text=True,
            timeout=25,
        )
        return r.returncode == 0
    except Exception:
        return False


def _collect_demucs_stems(out_dir: Path, expected: list[str]) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for wav in sorted(out_dir.rglob("*.wav")):
        key = wav.stem.lower()
        if key in expected and key not in mapping:
            mapping[key] = wav
    return mapping


def _demucs_shifts_arg() -> list[str]:
    """
    Più shifts = migliore qualità (media su più versioni leggermente spostate del brano), molto più lento.
    Default alto per qualità massima. DEMUCS_SHIFTS=0 disattiva (più veloce, peggiore).
    """
    raw = os.environ.get("DEMUCS_SHIFTS", "10").strip()
    if not raw or raw == "0":
        return []
    try:
        n = int(raw)
    except ValueError:
        n = 10
    if n <= 0:
        return []
    n = min(n, 20)
    return ["--shifts", str(n)]


def _parse_progress_from_line(line: str) -> int | None:
    """Estrae una percentuale da righe tqdm / Demucs se presente."""
    m = re.search(r"(\d{1,3})%\s*\|", line)
    if m:
        return min(100, max(0, int(m.group(1))))
    m2 = re.search(r"\[.*?(\d{1,3})%\]", line)
    if m2:
        return min(100, max(0, int(m2.group(1))))
    m3 = re.search(r"(\d+)\s*/\s*(\d+)\s*\[", line)
    if m3:
        cur, tot = int(m3.group(1)), int(m3.group(2))
        if tot > 0:
            return min(99, max(0, int(100 * cur / tot)))
    return None


def _run_demucs_streaming(
    model: str,
    wav_in: Path,
    out_dir: Path,
    expected: list[str],
    job_dir: Path,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = [
        sys.executable,
        "-m",
        "demucs",
        "-n",
        model,
        "-o",
        str(out_dir),
        *_demucs_shifts_arg(),
        str(wav_in),
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    write_separation_progress(job_dir, 4, "Avvio Demucs…")

    proc = subprocess.Popen(
        cmd,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    last_pct = 4

    def drain_stderr() -> None:
        nonlocal last_pct
        if proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                line = line.rstrip()
                if not line:
                    continue
                pct = _parse_progress_from_line(line)
                if pct is not None and pct > last_pct:
                    last_pct = pct
                    write_separation_progress(job_dir, last_pct, line[:400])
        except Exception as exc:
            logger.debug("stderr drain: %s", exc)

    t = threading.Thread(target=drain_stderr, daemon=True)
    t.start()
    try:
        rc = proc.wait(timeout=28800)
    finally:
        if proc.stderr:
            try:
                proc.stderr.close()
            except Exception:
                pass
        t.join(timeout=3)

    if rc != 0:
        raise RuntimeError("Demucs terminato con errore (codice %s)." % rc)

    write_separation_progress(job_dir, 92, "Raccolta file stem…")
    found = _collect_demucs_stems(out_dir, expected)
    if not set(expected).issubset(found.keys()):
        raise RuntimeError("Output Demucs incompleto.")
    return found


def _demo_stems(wav_in: Path, stems_dir: Path, stem_names: list[str]) -> dict[str, Path]:
    stems_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for name in stem_names:
        dest = stems_dir / f"{name}.wav"
        shutil.copy2(wav_in, dest)
        out[name] = dest
    return out


def separate(
    wav_in: Path,
    job_dir: Path,
    model_id: str,
    prefer_demucs: bool = True,
) -> tuple[dict[str, Path], bool, str | None]:
    """
    Ritorna (stems, used_demucs, warning_message).
    warning_message valorizzata in modalità demo.
    Aggiorna separation_progress.json durante l’elaborazione.
    """
    stems_dir = job_dir / "stems"
    model = next((m for m in DEMUCS_MODELS if m["id"] == model_id), DEMUCS_MODELS[0])
    names: list[str] = list(model["stems"])

    write_separation_progress(job_dir, 1, "Preparazione…")

    if stems_dir.exists():
        shutil.rmtree(stems_dir, ignore_errors=True)

    if prefer_demucs and demucs_cli_ok():
        try:
            raw = job_dir / "demucs_raw"
            if raw.exists():
                shutil.rmtree(raw, ignore_errors=True)
            found = _run_demucs_streaming(model_id, wav_in, raw, names, job_dir)
            stems_dir.mkdir(parents=True, exist_ok=True)
            out: dict[str, Path] = {}
            for stem, src in found.items():
                dest = stems_dir / f"{stem}.wav"
                shutil.copy2(src, dest)
                out[stem] = dest
            write_separation_progress(job_dir, 100, "Completato.")
            return out, True, None
        except Exception as exc:
            logger.warning("Demucs non riuscito, passo a modalità demo: %s", exc)
            write_separation_progress(job_dir, 6, f"Demucs fallito, uso demo: {exc}"[:300])

    demo_msg = (
        "Separazione DEMO: ogni traccia è una copia IDENTICA del brano intero. "
        "Per questo abbassare un singolo fader non “toglie” strumenti diversi (senti ancora tutto dagli altri canali). "
        "Installa sul server Demucs + PyTorch (vedi requirements-ml.txt) oppure usa il deploy Docker."
    )
    write_separation_progress(job_dir, 40, "Separazione in modalità demo (istantanea)…")
    result = _demo_stems(wav_in, stems_dir, names)
    write_separation_progress(job_dir, 100, "Completato (demo).")
    return result, False, demo_msg


def write_manifest(job_dir: Path, stems: dict[str, Path]) -> Path:
    manifest = job_dir / "manifest.json"
    rel = {k: str(v.relative_to(job_dir)) for k, v in stems.items()}
    manifest.write_text(json.dumps(rel, indent=2), encoding="utf-8")
    return manifest


def wav_duration(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        return frames / float(rate)
