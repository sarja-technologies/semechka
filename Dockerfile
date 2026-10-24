# syntax=docker/dockerfile:1
# NOTE: written against the Docker docs but not built in the authoring environment (no Docker available there).
FROM node:24-slim AS web
WORKDIR /w
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SEMECHKA_DATA_DIR=/data SEMECHKA_HOST=0.0.0.0 SEMECHKA_PORT=8780
RUN useradd --create-home --uid 10001 semechka && mkdir /data && chown semechka /data
WORKDIR /app
COPY backend/ /app/backend/
COPY --from=web /w/build /app/backend/semechka/web
RUN pip install --no-cache-dir /app/backend
USER semechka
VOLUME /data
EXPOSE 8780
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8780/api/health',timeout=2)"
CMD ["semechka", "serve"]
