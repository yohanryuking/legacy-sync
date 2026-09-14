from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuracion de la aplicacion, leida de variables de entorno / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://legacy_sync:legacy_sync@localhost:5433/legacy_sync"
    seed_record_count: int = 2000
    simulated_load_failure_rate: float = 0.03


@lru_cache
def get_settings() -> Settings:
    return Settings()
