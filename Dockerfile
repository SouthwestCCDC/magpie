# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies (cached layer)
FROM base AS deps
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project

# Final image
FROM base AS runtime
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install gosu for privilege dropping
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/* && \
    gosu nobody true && gosu 1000:1000 true  # verify it works

# Install AWS CLI v2 for S3 sync operations (multi-arch support)
RUN set -eux; \
    apt-get update && \
    apt-get install -y --no-install-recommends curl unzip && \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) aws_arch="x86_64" ;; \
        arm64) aws_arch="aarch64" ;; \
        *) echo "Unsupported architecture for AWS CLI: $arch" >&2; exit 1 ;; \
    esac; \
    curl "https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install && \
    rm -rf awscliv2.zip aws && \
    apt-get purge -y curl unzip && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

COPY src/ ./src/
COPY pyproject.toml uv.lock README.md ./

# Install the project itself
RUN uv sync --frozen --no-dev

# `uv sync` installs magpie-ctl's own console-script entry point at
# /app/.venv/bin/magpie-ctl (from pyproject.toml's [project.scripts]).
# Removed: PATH puts /app/.venv/bin ahead of /usr/local/bin (see ENV PATH
# above), so a bare `magpie-ctl` on PATH would resolve to this raw binary
# INSTEAD OF /usr/local/bin/magpie-ctl (the privilege-dropping wrapper
# below) -- silently bypassing it for any `docker exec <container>
# magpie-ctl ...` invocation (root, no gosu drop) while still appearing
# to work, since unrestricted root's implicit DAC_OVERRIDE masks the
# wrong uid. Every documented `docker exec ... magpie-ctl` usage (see
# docs/, deployment/README.md, scripts/magpie-deploy.sh) depends on the
# wrapper actually being what bare `magpie-ctl` resolves to.
RUN rm /app/.venv/bin/magpie-ctl

# Entrypoint handles dropping privileges to the correct user
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Wrapper for magpie-ctl that drops privileges when invoked via docker exec
COPY magpie-ctl-wrapper.sh /usr/local/bin/magpie-ctl
RUN chmod +x /usr/local/bin/magpie-ctl

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "magpie.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
