import pytest

from app.config import Settings


@pytest.mark.parametrize("scheme", ["postgresql", "postgres", "postgresql+psycopg"])
def test_postgres_connection_uses_installed_driver_and_preserves_url(scheme: str) -> None:
    suffix = "demo:p%40ss@db.example.test:5432/shop?sslmode=require&channel_binding=require"
    settings = Settings(database_url=f"{scheme}://{suffix}")
    assert settings.database_url == f"postgresql+psycopg://{suffix}"
