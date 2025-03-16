FROM python:3.12-slim

# Set the working directory
WORKDIR /app

# Copy the pyproject.toml and other necessary files
COPY pyproject.toml ./

# Install dependencies for postgres
RUN apt-get update && apt-get install -y libpq-dev && apt-get install -y gcc

# Install dependencies
RUN pip install .

# Copy the rest of the application code
COPY . .

# Command to run the application
CMD ["python", "main.py"]