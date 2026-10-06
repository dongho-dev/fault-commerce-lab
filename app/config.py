from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Fault Commerce Lab L1"
    database_url: str = "postgresql+psycopg://commerce:commerce@localhost:5432/commerce"
    observability_level: Literal["basic", "detailed"] = "basic"
    log_file: Path = Path("logs/app.jsonl")
    database_pool_size: int = Field(default=30, ge=1, le=80)
    database_max_overflow: int = Field(default=30, ge=0, le=80)

    @field_validator("database_url")
    @classmethod
    def normalize_postgres_driver(cls, value: str) -> str:
        for prefix in ("postgresql://", "postgres://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
