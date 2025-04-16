FROM python:3.12-slim

# Set the working directory
WORKDIR /app

# Copy the pyproject.toml and other necessary files
COPY pyproject.toml ./

# Install dependencies for postgres
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    apt-get update && \
    apt-get install -y libpq-dev gcc && \
    # Clean up apt cache to reduce image size
    rm -rf /var/lib/apt/lists/*

# Install dependencies
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    # Use --no-cache-dir to reduce image size
    pip install --no-cache-dir .

# Copy the rest of the application code
COPY . .

# Command to run the application
CMD ["python", "main.py"]