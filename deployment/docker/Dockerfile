# FSOCIETY AI — CPU / platform image.
# Contains the API, dashboard and model inventory. Model modification requires the
# engine image (torch/transformers) and is intentionally not installed here.
FROM node:22-alpine AS dashboard
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FSOCIETY_WORKSPACE=/data \
    FSOCIETY_API_HOST=0.0.0.0 \
    FSOCIETY_API_PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 fsociety

WORKDIR /app
COPY deployment/docker/requirements-platform.txt ./
RUN pip install --no-cache-dir -r requirements-platform.txt

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

COPY --from=dashboard /web/dist ./frontend/dist
RUN mkdir -p /data && chown -R fsociety:fsociety /data /app

USER fsociety
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)); raise SystemExit(0 if d.get('status') in ('ok','degraded') else 1)"

CMD ["fsociety", "serve", "--host", "0.0.0.0", "--port", "8000"]
