# Stage 1: builder — install dependencies into a virtual environment
FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen

# Stage 2: runtime — lean image with non-root user
FROM python:3.12-slim
RUN groupadd -r appuser && useradd -r -g appuser appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src/ src/
COPY alembic.ini .
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"
CMD ["uvicorn", "src.api.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--factory"]
