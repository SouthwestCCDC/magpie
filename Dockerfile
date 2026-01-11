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
    gosu nobody true  # verify it works

COPY src/ ./src/
COPY pyproject.toml uv.lock README.md ./

# Install the project itself
RUN uv sync --frozen --no-dev

# Entrypoint handles dropping privileges to the correct user
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Wrapper for magpie-ctl that drops privileges when invoked via docker exec
COPY magpie-ctl-wrapper.sh /usr/local/bin/magpie-ctl
RUN chmod +x /usr/local/bin/magpie-ctl

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "magpie.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
