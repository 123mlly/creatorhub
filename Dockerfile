FROM python:3.12-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CREATORHUB_DOCKER=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
      libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
      libgbm1 libasound2 libpango-1.0-0 libcairo2 libx11-xcb1 libxshmfence1 \
      fonts-liberation fonts-noto-cjk \
      nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock package.json package-lock.json ./
RUN uv sync --frozen --no-dev \
 && uv run playwright install chromium \
 && npm install --omit=dev --no-audit --no-fund \
 && rm -rf /root/.cache/uv /tmp/*

COPY app ./app
COPY config.example.yaml creatorhub.py selftest.py ./
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
 && mkdir -p /app/data/media /app/data/profiles

EXPOSE 8000
VOLUME ["/app/data"]

ENTRYPOINT ["/entrypoint.sh"]
