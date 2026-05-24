# syntax=docker/dockerfile:1.7
# Multi-stage build: uv builder produces .venv; slim runtime copies it.

# ---------- Stage 1: builder ----------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install runtime deps first for cache friendliness
COPY pyproject.toml ./
COPY uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev

# Now copy the source and install the project itself
COPY src/ ./src/
COPY .streamlit/ ./.streamlit/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim-bookworm AS runtime

# tzdata required for zoneinfo (Pitfall 4); ca-certificates for HTTPS to Telegram/Meta/GA4
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

# Copy the venv, source, and streamlit config from the builder stage
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src
COPY --from=builder --chown=app:app /app/.streamlit /app/.streamlit

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/metrics.db

# Persistent SQLite volume (Pitfall 7: never write DB to ephemeral fs)
RUN mkdir -p /data && chown app:app /data
VOLUME ["/data"]

USER app

# Healthcheck: confirm the DB file is reachable. Phase 5 may promote to a richer probe.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import os, sys; sys.exit(0 if os.path.exists(os.environ.get('DB_PATH','/data/metrics.db')) else 1)"

CMD ["python", "-m", "src"]
