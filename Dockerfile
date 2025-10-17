FROM python:3.12-alpine3.19

# Install curl for healthcheck and build dependencies
RUN apk update && \
    apk add curl gcc musl-dev libffi-dev && \
    rm -rf /var/cache/apk/*

HEALTHCHECK --interval=1m --timeout=10s --retries=3 --start-period=1m \
    CMD curl --fail localhost:8000/api/manager/healthcheck || exit 1

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set up the venv
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install dependencies:
COPY requirements.txt .
RUN uv pip install --no-cache -r requirements.txt

# Run the application:
COPY . .
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0"]
