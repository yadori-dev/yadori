ARG BASE_IMAGE=ubuntu:24.04
FROM ghcr.io/astral-sh/uv:0.11.18 AS uv
FROM debian:13-slim AS certificates
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates
FROM ${BASE_IMAGE} AS os
COPY --from=certificates /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
RUN sed -i 's,http://,https://,g' /etc/apt/sources.list.d/*.sources \
    && if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then \
        sed -i -e 's/^Components:.*/Components: main/' \
            -e 's/security.ubuntu.com/archive.ubuntu.com/g' /etc/apt/sources.list.d/ubuntu.sources; \
    fi \
    && printf 'Acquire::https::CAInfo "/etc/ssl/certs/ca-certificates.crt"; Acquire::ForceIPv4 "true"; Acquire::Retries "2"; Acquire::http::Timeout "20"; Acquire::https::Timeout "20";\n' > /etc/apt/apt.conf.d/99-build-network
FROM os AS build
RUN apt-get update -o APT::Update::Error-Mode=any && apt-get install -y --no-install-recommends \
    python3 ca-certificates dpkg-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY dist/*.whl /tmp/wheels/
RUN uv export --locked --no-dev --no-emit-project -o /tmp/requirements.txt
RUN uv venv --python /usr/bin/python3 /usr/lib/yadori/venv \
    && uv pip sync --python /usr/lib/yadori/venv/bin/python --require-hashes --no-build /tmp/requirements.txt \
    && uv pip install --python /usr/lib/yadori/venv/bin/python --compile-bytecode --no-deps --no-build /tmp/wheels/*.whl
COPY assets/branding/yadori-icon-v2.png /tmp/yadori.png
COPY packaging/apt/build.sh /tmp/build.sh
ARG PACKAGE_VERSION
RUN bash /tmp/build.sh "$PACKAGE_VERSION"
FROM scratch AS package
COPY --from=build /out/ /
