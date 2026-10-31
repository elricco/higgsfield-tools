# hf as a service for agents – see docs/server.md
FROM python:3.14-slim

# PYTHONUNBUFFERED: log lines appear immediately in "docker logs".
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_DATA_DIR=/data

# Run as an unprivileged user; /data holds the job log (hf.db) and the stored results.
RUN useradd --create-home --uid 10001 hf && mkdir /data && chown hf:hf /data

WORKDIR /app
COPY --chown=hf:hf . .
# Editable install, like setup.sh: models.json, catalog.json and prices.json stay next to the code.
RUN pip install -e .

USER hf
# 8080: API for agents (keep internal). 8081: webhook only, if HF_WEBHOOK_LISTEN=0.0.0.0:8081.
EXPOSE 8080 8081
VOLUME ["/data"]
# Checks the port from HF_LISTEN (default 8080), so the health check follows a changed port.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os, urllib.request; port = os.environ.get('HF_LISTEN', '0.0.0.0:8080').rpartition(':')[2]; urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=4)"
CMD ["hf", "serve"]
