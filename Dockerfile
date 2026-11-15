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
    PORT=3000 \
    API_PORT=8000 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    HNIDS_API_KEY=dev-local-key

# Copy project specification files
COPY pyproject.toml uv.lock ./

# Install python dependencies using uv (CPU torch)
RUN uv sync --frozen --no-dev --all-extras

# Copy application source, models, and frontend
COPY src/ ./src/
COPY configs/ ./configs/
COPY models/ ./models/
COPY examples/ ./examples/
COPY frontend/ ./frontend/
COPY serve_dashboard.py ./
COPY start.sh ./

# Set permissions
RUN chmod +x ./start.sh

# Expose web service ports (3000 for standard/Render, 7860 for Hugging Face Spaces)
EXPOSE 3000 7860

# Health check against proxy readiness
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -f http://127.0.0.1:${PORT}/api/readyz || exit 1

# Start unified services
CMD ["./start.sh"]
