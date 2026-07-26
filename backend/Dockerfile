# Sports Predictor API
#
# The SQLite database lives at DATA_DIR (default /data) so it can sit on a
# mounted volume. wc_match_log holds the live World Cup track record — 104
# matches predicted before kickoff — which is unreproducible by definition.
# Deploying this image WITHOUT a persistent volume silently discards that
# record on every deploy.

FROM python:3.12-slim

WORKDIR /app

# Layer-cache the dependency install; scipy/numpy ship manylinux wheels so no
# build toolchain is needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY main.py .

# Non-root: the app only needs to read its code and write the data dir.
RUN useradd --create-home appuser && mkdir -p /data && chown appuser /data
USER appuser

ENV DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

# Single worker on purpose: the rate limiters, daily budget, and calibration
# cache are in-process, and SQLite prefers one writer. Scale up only after
# moving those to shared storage.
CMD ["sh", "-c", "uvicorn main:app --host $HOST --port $PORT --workers 1"]
