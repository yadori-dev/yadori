ARG BASE_IMAGE=ubuntu:24.04
FROM ghcr.io/astral-sh/uv:0.11.18 AS uv
FROM ${BASE_IMAGE} AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 ca-certificates dpkg-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY backend/src backend/src
RUN uv export --locked --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv build --wheel --out-dir /tmp/wheels
RUN uv venv --python /usr/bin/python3 /usr/lib/yadori/venv \
    && uv pip sync --python /usr/lib/yadori/venv/bin/python --require-hashes --no-build /tmp/requirements.txt \
    && uv pip install --python /usr/lib/yadori/venv/bin/python --compile-bytecode --no-deps --no-build /tmp/wheels/*.whl
COPY assets/branding/yadori-icon-v2.png /tmp/yadori.png
COPY packaging/apt/build.sh /tmp/build.sh
ARG PACKAGE_VERSION
RUN bash /tmp/build.sh "$PACKAGE_VERSION"
FROM scratch AS package
COPY --from=build /out/ /
