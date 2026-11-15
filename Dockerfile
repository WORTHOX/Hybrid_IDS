# ==============================================================================
# H_IDS — Hybrid Network Intrusion Detection System
# Multi-stage lightweight Docker image for production & cloud deployment
# ==============================================================================

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=10000 \
    API_PORT=8000 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    HNIDS_API_KEY=dev-local-key

# Copy dependency specifications and README (required by hatchling packaging)
COPY pyproject.toml uv.lock README.md ./

# Step 1: Install all Python dependencies first (torch-cpu, xgboost, fastapi, etc.)
RUN uv sync --frozen --no-install-project --no-dev --all-extras

# Step 2: Copy application source, models, configs, and frontend
COPY src/ ./src/
COPY configs/ ./configs/
COPY models/ ./models/
COPY examples/ ./examples/
COPY frontend/ ./frontend/
COPY serve_dashboard.py ./
COPY start.sh ./

# Step 3: Install the hnids CLI package
RUN uv sync --frozen --no-dev --all-extras

# Set permissions
RUN chmod +x ./start.sh

# Expose web service ports (10000 for Render default, 3000 for local)
EXPOSE 10000 3000

# Health check against proxy readiness
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -f http://127.0.0.1:${PORT}/api/readyz || exit 1

# Start unified services
CMD ["./start.sh"]
