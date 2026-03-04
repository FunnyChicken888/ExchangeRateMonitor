# ── Build stage ────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

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

RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
        git \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY src/ ./src/
COPY run.py ./run.py
COPY .git/ ./.git/

# config.json 不打包進 image（含機密）— 請掛載為 volume：
#   -v /volume1/docker/ExchangeRateMonitor/config.json:/app/config.json:ro
# state.json 與 logs 也建議掛載，避免重啟後資料遺失

# Create directories for named volumes
RUN mkdir -p /app/logs /app/data

ENV TZ=Asia/Taipei
ENV CONFIG_PATH=/app/config.json
ENV STATE_PATH=/app/state.json
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

HEALTHCHECK --interval=120s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os, sys; sys.exit(0 if os.path.exists('/app/state.json') else 1)"

RUN useradd -r -u 1001 -g root monitor
RUN chown -R monitor:root /app
USER monitor

CMD ["python", "run.py"]
