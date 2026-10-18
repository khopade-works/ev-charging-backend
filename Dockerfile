# syntax=docker/dockerfile:1
# Base images pinned by digest: rebuilds are reproducible, and Dependabot proposes digest bumps.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS deps
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /venv && /venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.source="https://github.com/khopade-works/ev-charging-backend" \
      org.opencontainers.image.revision="${GIT_SHA}"
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=deps /venv /venv
WORKDIR /app
COPY ev ./ev
ENV PATH=/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EV_GIT_SHA=${GIT_SHA} \
    EV_METRICS_PORT=9000
USER 10001:10001
EXPOSE 8000 9000
# One process per container: replicas scale out as pods. --no-access-log: the app logs requests as JSON with trace ids.
CMD ["uvicorn", "ev.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
