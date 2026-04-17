from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def _which(name: str) -> str | None:
    return shutil.which(name)


def extract_audio_from_video(video_path: Path, wav_out: Path) -> None:
    """Estrae stereo WAV PCM 48kHz per analisi e separazione."""
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg non trovato nel PATH. Installa FFmpeg.")
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-c:a",
        "pcm_s24le",
        str(wav_out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def probe_duration_seconds(path: Path) -> float:
    ffprobe = _which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe non trovato nel PATH.")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    data = json.loads(out.stdout)
    return float(data["format"]["duration"])


def normalize_to_wav_48k_stereo(src: Path, wav_out: Path) -> None:
    """Converte audio in WAV 48kHz stereo 24-bit."""
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg non trovato nel PATH.")
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-ac",
        "2",
        "-ar",
        "48000",
        "-c:a",
        "pcm_s24le",
        str(wav_out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def export_stem(
    src_wav: Path,
    dest: Path,
    fmt: str,
) -> None:
    """Export singolo stem; fmt: wav24, flac, aiff, mp3_320, aac_256, opus."""
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg non trovato nel PATH.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    base = ["ffmpeg", "-y", "-i", str(src_wav)]
    if fmt == "wav24":
        cmd = base + ["-c:a", "pcm_s24le", str(dest)]
    elif fmt == "flac":
        cmd = base + ["-c:a", "flac", "-compression_level", "12", str(dest)]
    elif fmt == "aiff":
        cmd = base + ["-c:a", "pcm_s24be", str(dest)]
    elif fmt == "mp3_320":
        cmd = base + ["-c:a", "libmp3lame", "-b:a", "320k", str(dest)]
    elif fmt == "aac_256":
        cmd = base + ["-c:a", "aac", "-b:a", "256k", str(dest)]
    elif fmt == "opus":
        cmd = base + ["-c:a", "libopus", "-b:a", "256k", str(dest)]
    else:
        raise ValueError(f"Formato non supportato: {fmt}")
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def mix_wavs_with_volumes(
    stem_paths: list[tuple[Path, float]],
    dest: Path,
    fmt: str,
) -> None:
    """Mixa più WAV con volumi lineari (0–1+)."""
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg non trovato nel PATH.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for p, _ in stem_paths:
        inputs += ["-i", str(p)]
    n = len(stem_paths)
    vols = []
    for i, (_, g) in enumerate(stem_paths):
        # volume in dB approx da gain lineare, evita log(0)
        import math

        if g <= 0:
            db = -96
        else:
            db = 20 * math.log10(g)
        vols.append(f"[{i}:a]volume={db:.4f}dB[a{i}]")
    chain = ";".join(vols)
    mix = "".join(f"[a{i}]" for i in range(n))
    filt = f"{chain};{mix}amix=inputs={n}:duration=longest:dropout_transition=0[out]"
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filt, "-map", "[out]"]
    if fmt == "wav24":
        cmd += ["-c:a", "pcm_s24le", str(dest)]
    elif fmt == "flac":
        cmd += ["-c:a", "flac", "-compression_level", "12", str(dest)]
    elif fmt == "aiff":
        cmd += ["-c:a", "pcm_s24be", str(dest)]
    elif fmt == "mp3_320":
        cmd += ["-c:a", "libmp3lame", "-b:a", "320k", str(dest)]
    elif fmt == "aac_256":
        cmd += ["-c:a", "aac", "-b:a", "256k", str(dest)]
    elif fmt == "opus":
        cmd += ["-c:a", "libopus", "-b:a", "256k", str(dest)]
    else:
        raise ValueError(f"Formato non supportato: {fmt}")
    subprocess.run(cmd, check=True, capture_output=True, text=True)
