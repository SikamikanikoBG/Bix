# Bix server (portal + device API + worker). Build from software/:
#   docker build -f deploy/docker/Dockerfile -t bix-server .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 PIP_NO_CACHE_DIR=1 \
    BIX_DATA_DIR=/data BIX_SERVER_CONFIG=/data/server.toml BIX_LOG_JSON=1 \
    BIX_HTTP_HOST=0.0.0.0 BIX_HTTP_PORT=8710

# Corporate network: the bank's CA (for a TLS-inspecting proxy, the SMTP relay, private endpoints)
# and a PyPI mirror. Proxies come in as the usual HTTP(S)_PROXY build args.
COPY deploy/docker/ca-certificates/ /usr/local/share/ca-certificates/
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt PIP_CERT=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update \
 && apt-get install -y --no-install-recommends libsndfile1 libportaudio2 ffmpeg ca-certificates \
 && update-ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --home /data bix \
 && mkdir -p /data && chown bix:bix /data   # new volumes inherit this owner; the app runs as bix

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# local-stt adds faster-whisper for private-cloud deployments without Azure.
ARG EXTRAS=server,azure
RUN pip install ".[${EXTRAS}]"
# The speaker models (~45 MB) are fetched at build time: the server may have no route to GitHub.
ARG PRELOAD_MODELS=1
ENV BIX_MODELS_PATH=/opt/bix/models
RUN if [ "$PRELOAD_MODELS" = "1" ]; then python -c "from pathlib import Path; from bix.common import models; models.ensure(Path('/opt/bix/models'), 'nemo_en_titanet_small.onnx')"; fi

USER bix
VOLUME ["/data"]
EXPOSE 8710
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8710/healthz').status==200 else 1)"
CMD ["bix", "server"]
