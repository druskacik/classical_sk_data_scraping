FROM python:3.12-slim

# Set the working directory
WORKDIR /app

# Copy the pyproject.toml and other necessary files
COPY pyproject.toml ./

# Install dependencies for postgres
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    apt-get update && \
    apt-get install -y libpq-dev gcc curl ca-certificates git && \
    # Clean up apt cache to reduce image size
    rm -rf /var/lib/apt/lists/*

# Install the standalone Codex CLI used by the scheduled programme analyzer.
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    curl -fsSL https://chatgpt.com/codex/install.sh | sh && \
    install -m 0755 /root/.local/bin/codex /usr/local/bin/codex

# Install dependencies
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    # Use --no-cache-dir to reduce image size
    pip install --no-cache-dir .

# Copy the rest of the application code
COPY . .

# Install the vendored experimental Codex Python SDK without pulling a second
# CLI package; the standalone binary above is the runtime used by the SDK.
RUN pip install --no-cache-dir --no-deps -e vendor/codex/sdk/python

# Apply database migrations before starting the application.
CMD ["sh", "-c", "alembic upgrade head && exec python main.py"]
