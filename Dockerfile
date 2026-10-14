# Build stage: resolve dependencies with uv into a venv
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable

# Runtime stage: the venv only, no build tools, no source tree
FROM python:3.13-slim
RUN groupadd --gid 1000 app \
 && useradd --uid 1000 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data /config \
 && chown app:app /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    CONFIG_FILE=/config/config.yaml \
    DATA_DIR=/data \
    PORT=8000
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
USER app
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ['PORT'], timeout=4).status == 200 else 1)"]
ENTRYPOINT ["micropub-server"]
CMD ["serve"]
