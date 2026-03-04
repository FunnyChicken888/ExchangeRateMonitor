# ── Build stage ────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libmariadb-dev-compat \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL maintainer="ExchangeRateMonitor"
LABEL description="24/7 spread monitor: MAX USDT/TWD vs NextBank USD sell rate"

WORKDIR /app

# Runtime system dependencies (MariaDB client libs for PyMySQL)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY src/ ./src/

# Bake in config.json so NAS deployment needs no volume mount for config
COPY config.json ./config.json

# Create directories for named volumes (state + logs)
RUN mkdir -p /app/logs /app/data

# Default environment variables (overridable via docker-compose)
ENV TZ=Asia/Taipei
ENV CONFIG_PATH=/app/config.json
ENV STATE_PATH=/app/state.json
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check — verify the process is alive
HEALTHCHECK --interval=120s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os, sys; sys.exit(0 if os.path.exists('/app/state.json') else 1)"

# Run as non-root user for security
RUN useradd -r -u 1001 -g root monitor
RUN chown -R monitor:root /app
USER monitor

CMD ["python", "-m", "src.main"]
