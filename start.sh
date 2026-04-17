#!/usr/bin/env bash
# Avvio unico: sito + API sulla porta 8765, stem reali (Demucs) se i pacchetti ML sono installati.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Errore: serve python3 nel PATH."
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Avviso: ffmpeg non trovato. Installalo (es. brew install ffmpeg) per video e export."
fi
if ! command -v node >/dev/null 2>&1; then
  echo "Errore: serve Node.js (node) nel PATH."
  exit 1
fi

echo "==> Ambiente Python (backend/.venv)"
if [[ ! -d backend/.venv ]]; then
  python3 -m venv backend/.venv
fi
# shellcheck source=/dev/null
source backend/.venv/bin/activate

echo "==> Dipendenze backend + Demucs (può richiedere diversi minuti la prima volta)"
python3 -m pip install -q --upgrade pip
python3 -m pip install -r backend/requirements.txt -r backend/requirements-ml.txt

echo "==> Verifica Demucs (se fallisce, su Mac installa torch da https://pytorch.org poi: pip install demucs)"
python3 -m demucs -h >/dev/null

echo "==> Build frontend"
if [[ ! -d frontend/node_modules ]]; then
  (cd frontend && npm install)
fi
(cd frontend && npm run build)

export STEM_STATIC_DIR="$ROOT/frontend/dist"
export STEM_STUDIO_DATA="${STEM_STUDIO_DATA:-$ROOT/backend/data}"
cd "$ROOT/backend"

echo "==> App pronta: apri nel browser  http://127.0.0.1:8765"
echo "    (Ctrl+C per uscire)"
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
