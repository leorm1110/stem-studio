# Stem Studio

Webapp per separazione stem (voce, basso, batteria, ecc.), pronta per **Capacitor** (build Android/iOS). Include mixer stile DAW, export ZIP / mix, e area **Developer** per coppie mix + stem di riferimento.

## Architettura

- **frontend/** — React + Vite + TypeScript, UI upload → analisi → separazione → mixer → export.
- **backend/** — FastAPI: estrazione audio da video (FFmpeg), separazione tramite **Demucs** (se installato), export ad alta qualità, dataset developer in **SQLite** + file su disco.

## Requisiti di sistema

- **Node.js** 18+
- **Python** 3.10+
- **FFmpeg** nel `PATH` (obbligatorio per video e export).
- **Demucs** (opzionale ma consigliato per separazione reale): dopo `pip install -r requirements.txt`, installa anche PyTorch per la tua piattaforma da [pytorch.org](https://pytorch.org), poi `pip install demucs`. Senza Demucs il backend risponde in modalità **demo** (stem sintetici per provare la UI).

## Avvio rapido

```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8765

# Frontend (altro terminale)
cd frontend
npm install
npm run dev
```

Apri l’URL indicato da Vite (di solito `http://localhost:5173`). In `frontend/vite.config.ts` il proxy punta a `http://127.0.0.1:8765`.

## Mobile (Capacitor)

```bash
cd frontend
npm run build
npx cap add android   # o ios
npx cap sync
npx cap open android
```

Configura URL del backend: in produzione imposta `VITE_API_BASE` (es. `https://api.tuodominio.it`) prima di `npm run build`.

## Database e archivio esterni (funzione Developer)

**Sì**: per migliaia di caricamenti servono:

1. **Database** (qui: SQLite in dev; in produzione **PostgreSQL**) per metadati: titolo, hash file, percorsi, stato, timestamp.
2. **Object storage** (S3, GCS, MinIO) o volume dedicato per i file audio — non tenere blob enormi nel DB.

L’app salva le coppie developer su disco (`backend/data/developer/`) e i record in SQLite. Il vero “apprendimento continuo” richiede una **pipeline di training** (job GPU, versionamento modelli); il codice predispone API e tabella `training_jobs` per collegarla in seguito.

## Qualità export

Il backend usa FFmpeg con PCM a 24 bit per WAV, qualità massima per FLAC, e parametri conservativi per AAC/MP3/M4A/Opus dove applicabile.
