# ---- frontend build -------------------------------------------------------
FROM node:22-alpine3.20 AS web-build
WORKDIR /build
ENV npm_config_registry=https://registry.npmmirror.com \
    COREPACK_NPM_REGISTRY=https://registry.npmmirror.com
COPY web/package.json web/pnpm-lock.yaml* ./
RUN corepack enable && corepack prepare pnpm@9.15.4 --activate && pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# ---- python runtime --------------------------------------------------------
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# pdfplumber/pikepdf runtime needs build tools not required at runtime;
# wheels are used so no compiler is necessary.
WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
RUN pip install --no-cache-dir . \
    && rm -rf /app/build /app/tabledger.egg-info

COPY --from=web-build /build/dist /app/web-static

RUN useradd --system --create-home --uid 1000 tabledger \
    && mkdir -p /data \
    && chown tabledger:tabledger /data /app

USER tabledger

ENV TAB_DATA_DIR=/data \
    TAB_STATIC_DIR_OVERRIDE=/app/web-static

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
