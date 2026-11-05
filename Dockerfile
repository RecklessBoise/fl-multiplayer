# FL Multiplayer room server. Only the server runs here: no MIDI, no FL script.
# TLS (wss://) is terminated by the hosting platform (Fly.io, Render).
# Multi-arch index digest of python:3.12-slim-bookworm (update deliberately).
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/server:/app/fl_script \
    PORT=8080

WORKDIR /app

# Pinned and hash-checked dependencies (websockets only).
COPY server/requirements.txt server/requirements.txt
RUN pip install --require-hashes --no-deps -r server/requirements.txt

COPY fl_script/flmp_core fl_script/flmp_core
COPY server/flmp_server server/flmp_server

RUN useradd --system --uid 10001 --no-create-home flmp
USER flmp

EXPOSE 8080
CMD ["python", "-m", "flmp_server"]
