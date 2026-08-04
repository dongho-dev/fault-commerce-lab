FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY oracle ./oracle
COPY scripts ./scripts
COPY tools ./tools
COPY tests ./tests
COPY alembic.ini docker-entrypoint.sh ./

RUN pip install --upgrade pip \
    && pip install ".[dev]" \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

CMD ["/app/docker-entrypoint.sh"]
