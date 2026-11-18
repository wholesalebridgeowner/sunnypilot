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

# Pinned cereal/car.capnp points into the opendbc gitlink, whose historical
# commit is no longer fetchable from the public upstream Git remote. A byte-
# identical schema extracted through the connected GitHub API is vendored with
# its MIT license. Verify the original Git blob identity before compiling it.
RUN rm /opt/sunnypilot/cereal/car.capnp
COPY pinned-schema/car.capnp /opt/sunnypilot/cereal/car.capnp
RUN test "$(git hash-object /opt/sunnypilot/cereal/car.capnp)" = "d237e353d549800b2db6aff58f313e89799df958"

# Fail at build time if the pinned schema cannot resolve its imported capnp files.
RUN ls -la /opt/sunnypilot/cereal/ && test -s /opt/sunnypilot/cereal/car.capnp && test -s /opt/sunnypilot/cereal/log.capnp \
    && python -c 'import capnp; from pathlib import Path; p=Path("/opt/sunnypilot/cereal"); capnp.load(str(p/"car.capnp"), imports=[str(p)]); capnp.load(str(p/"log.capnp"), imports=[str(p)])'

WORKDIR /opt/ram
COPY . /opt/ram/
RUN python -m unittest discover -s /opt/ram -p 'test_private_route_probe.py' -v \
    && python -m unittest discover -s /opt/ram -p 'test_torque_limit_coverage.py' -v

# No daemon, HTTP port, uploads, or privileged device access.
CMD ["python", "-u", "/opt/ram/private_route_probe.py"]
