# syntax=docker/dockerfile:1
# PRED Agent — slim Python image, non-root user
# Grounded in kit/09_CONFIG_SECURITY_DEPLOY.md (Deploy section)

FROM python:3.11-slim AS builder

WORKDIR /build
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies for compiling wheels if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml backend/pytest.ini backend/alembic.ini ./backend/
COPY backend/alembic ./backend/alembic/
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-deps -e /build/backend 2>/dev/null || pip install -r /dev/null 2>/dev/null; \
    pip install "fastapi==0.115.0" "uvicorn[standard]==0.34.0" "httpx==0.27.2" \
        "pydantic==2.10.3" "pydantic-settings==2.6.1" "SQLAlchemy==2.0.36" "alembic==1.13.1" \
        "eth-account==0.13.2" "web3==7.10.0" "ably==3.1.4" "python-telegram-bot==21.4" \
        "structlog==24.4.0" "pyyaml==6.0.2" "python-dateutil==2.9.0.post0" \
        "pytest==8.3.3" "pytest-asyncio==0.26.0" "ruff==0.9.6" "mypy==1.13.0" "pip-audit==2.7.2"

# ---------------------------------------------------------------------------
# Runtime stage — slim, non-root user
# ---------------------------------------------------------------------------
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

# Install only runtime ca-certificates for TLS
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r predagent && useradd -r -g predagent -d /app -s /sbin/nologin predagent

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY backend/ ./backend/
COPY PLAN.md DOCS_FINDINGS.md ./

# Make sure alembic can find its config
ENV ALEMBIC_INI=/app/backend/alembic.ini

# Run migrations and start agent as non-root
USER predagent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)" || exit 1

CMD ["python", "-m", "pred_agent.main"]
