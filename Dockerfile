# Optional single-container build: React UI + API in one service (e.g. Hugging Face Spaces, Fly, a VPS).
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*
COPY backend/app ./app
COPY --from=web /web/dist ./static
RUN useradd -m appuser && mkdir -p /srv/data && chown -R appuser /srv
USER appuser
ENV DATA_DIR=/srv/data STATIC_DIR=/srv/static PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
