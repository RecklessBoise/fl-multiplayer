# FL Multiplayer room server. Only the server runs here: no MIDI, no FL script.
# TLS (wss://) is terminated by the hosting platform (Fly.io, Render).
FROM python:3.12-slim-bookworm

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
