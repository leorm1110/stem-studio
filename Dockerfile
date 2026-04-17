# Un solo servizio: API + sito web (frontend compilato in /app/static).
# Esempio Render / Railway / Fly: imposta PORT. DEMUCS_SHIFTS default alto = massima qualità (lento).

FROM node:20-bookworm-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_API_BASE=
RUN npm run build

FROM python:3.11-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/requirements-ml.txt /app/backend/requirements-ml.txt
RUN pip install --no-cache-dir -r /app/backend/requirements-ml.txt

COPY backend/ /app/backend/
COPY --from=frontend /src/dist /app/static

ENV STEM_STATIC_DIR=/app/static
ENV DEMUCS_SHIFTS=10
EXPOSE 8000

WORKDIR /app/backend
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
