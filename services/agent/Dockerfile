FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_SYSTEM_PYTHON=1
WORKDIR /app
# Pinned, not :latest — a resolver change between now and submission would rebuild this image
# differently from the one that was verified.
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY scenepilot ./scenepilot
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" SCENEPILOT_DATA_DIR=/tmp/scenepilot PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn scenepilot.api.app:app --host 0.0.0.0 --port ${PORT}"]
