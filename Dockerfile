# --- Build stage: compile wheels once -----------------------------------------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# --- Runtime stage: slim image, non-root user ---------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    MEDIA_DIR=/app/media
WORKDIR /app

RUN useradd --create-home --uid 10001 app
COPY --from=builder /wheels /wheels
RUN pip install --no-index /wheels/* && rm -rf /wheels

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY scripts/start.sh ./scripts/start.sh
RUN mkdir -p /app/media && chown -R app:app /app/media && chmod +x scripts/start.sh

USER app
EXPOSE 8000

# Liveness probe for Docker / docker compose (platforms like Render use their own).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)" || exit 1

CMD ["./scripts/start.sh"]
