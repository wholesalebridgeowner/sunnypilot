# Read-only, single-run Railway log analysis; NOT a Comma installer.
FROM python:3.11-slim-bookworm

ARG PINNED_SUNNYPILOT_COMMIT=1f7233cb983f3c13fdbba47a86f8fbc796ad9211
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PINNED_CEREAL_LOG_SCHEMA=/opt/sunnypilot/cereal/log.capnp

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --disable-pip-version-check \
    pycapnp==2.0.0 zstandard==0.23.0 boto3==1.35.99

# Shallow, sparse checkout of only the cereal schema used by the installed
# November 2026 Comma build. Do not decode its logs with the latest master.
RUN mkdir -p /opt/sunnypilot && cd /opt/sunnypilot \
    && git init -q \
    && git remote add origin https://github.com/sunnypilot/sunnypilot.git \
    && git sparse-checkout init --cone \
    && git sparse-checkout set cereal \
    && git fetch --depth=1 origin "${PINNED_SUNNYPILOT_COMMIT}" \
    && git checkout -q --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${PINNED_SUNNYPILOT_COMMIT}"

# Fail at build time if the pinned schema cannot resolve its imported capnp files.
RUN python -c 'import capnp; from pathlib import Path; p=Path("/opt/sunnypilot/cereal/log.capnp"); capnp.load(str(p), imports=[str(p.parent)])'

WORKDIR /opt/ram
COPY . /opt/ram/
RUN python -m unittest discover -s /opt/ram -p 'test_private_route_probe.py' -v

# No daemon, HTTP port, uploads, or privileged device access.
CMD ["python", "-u", "/opt/ram/private_route_probe.py"]
