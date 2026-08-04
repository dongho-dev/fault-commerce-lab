import os
from pathlib import Path


def resolve_database_url(explicit: str | None = None) -> str:
    if explicit is not None:
        value = explicit
    else:
        value = os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg://commerce:commerce@localhost:5432/commerce",
        )
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def ensure_artifact_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
