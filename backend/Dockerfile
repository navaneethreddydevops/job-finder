# Use astral uv pre-installed image for building dependencies
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Copy dependency definition files
COPY pyproject.toml uv.lock ./

# Install dependencies (cached layer)
RUN uv sync --frozen --no-install-project --no-dev

# Final stage
FROM python:3.12-slim
WORKDIR /app/backend

# Copy the pre-compiled virtual environment
COPY --from=builder /app/.venv /app/.venv

# Copy backend codebase
COPY backend /app/backend

# Setup path and env variables
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Start FastAPI using uvicorn binding to all interfaces
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
