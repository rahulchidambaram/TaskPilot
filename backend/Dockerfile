# Backend image for Render / Fly / any Docker host.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY app ./app

# Run as an unprivileged user (Chromium is launched with --no-sandbox, so the container itself is the sandbox).
RUN useradd -m appuser && mkdir -p /srv/data && chown -R appuser /srv
USER appuser

ENV DATA_DIR=/srv/data PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
