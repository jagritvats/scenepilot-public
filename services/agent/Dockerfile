FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_SYSTEM_PYTHON=1
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY scenepilot ./scenepilot
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" SCENEPILOT_DATA_DIR=/tmp/scenepilot PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn scenepilot.api.app:app --host 0.0.0.0 --port ${PORT}"]
