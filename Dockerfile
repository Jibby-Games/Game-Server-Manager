FROM python:3.13-alpine3.19

# Install curl for healthcheck
RUN apk update && \
    apk add curl && \
    rm -rf /var/cache/apk/*

HEALTHCHECK --interval=1m --timeout=10s --retries=3 --start-period=1m \
    CMD curl --fail localhost:8000/api/manager/healthcheck || exit 1

# Install uv (pinned for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

# Compile bytecode for faster startup; use copy link mode for cache mount compatibility
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies as a separate layer (rebuilt only when lockfile changes)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

# Copy the project and do final sync
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENTRYPOINT ["uv", "run", "fastapi", "run", "app/main.py", "--host", "0.0.0.0"]
