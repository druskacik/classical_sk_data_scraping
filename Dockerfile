FROM python:3.12-slim

# Set the working directory
WORKDIR /app

# Copy the pyproject.toml and other necessary files
COPY pyproject.toml ./

# Install dependencies for postgres
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    apt-get update && \
    apt-get install -y libpq-dev gcc ca-certificates git && \
    # Clean up apt cache to reduce image size
    rm -rf /var/lib/apt/lists/*

# Install dependencies
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    # Use --no-cache-dir to reduce image size
    pip install --no-cache-dir .

# Expose the Codex runtime bundled with the Python SDK for interactive use.
RUN ln -s "$(python -c 'from codex_cli_bin import bundled_codex_path; print(bundled_codex_path())')" \
    /usr/local/bin/codex

# Copy the rest of the application code
COPY . .

# Apply database migrations before starting the application.
CMD ["sh", "-c", "alembic upgrade head && exec python main.py"]
