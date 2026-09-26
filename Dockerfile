# Stage 1: Builder
FROM python:3.14.7-slim AS builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uv/bin/uv

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libcairo2-dev \
    pkg-config \
    python3-dev \
    libgirepository1.0-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies using uv
COPY pyproject.toml uv.lock ./
RUN /uv/bin/uv sync --frozen --no-install-project --no-dev


# Stage 2: Runtime
FROM python:3.14.7-slim AS runtime

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Enable the experimental CPython JIT
ENV PYTHON_JIT=1
# Add .venv/bin to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Set work directory
WORKDIR /app

# Install runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libgirepository-1.0-1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user for security
RUN groupadd -r kizuna && useradd -r -g kizuna kizuna

# Copy the virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv

# Copy project files
COPY . .

# Fix permissions
RUN chown -R kizuna:kizuna /app

# Switch to non-root user
USER kizuna

# Run the bot
ENTRYPOINT ["python", "-m", "chainnokizuna"]
