# Store-ready Relay image. Does not create DynamoDB tables.
# Build: docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t amusant-relay .
# uv is installed from PyPI, not GHCR.
FROM python:3.12-slim-bookworm
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.25
COPY pyproject.toml uv.lock ./
COPY fast_voice ./fast_voice
COPY relay ./relay
# This historical ACP/DynamoDB preflight image keeps the optional store adapter.
RUN uv sync --locked --no-dev --extra dynamodb
ENV PYTHONPATH=/app
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA
CMD ["/app/.venv/bin/python", "-m", "relay.wss_preflight_smoke"]
