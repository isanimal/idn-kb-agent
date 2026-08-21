"""Environment-backed application configuration."""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    """Validated settings loaded from environment variables."""

    model_config = ConfigDict(extra="ignore")

    app_env: str = "development"
    idn_base_url: str = "https://www.idn.id"
    idn_training_url: str = "https://www.idn.id/training/"
    kb_base_url: str = "https://kb.idn.id"
    kb_training_url: str = "https://kb.idn.id/kb/training"
    kb_training_create_url: str = "https://kb.idn.id/kb/training/edit?new=1"
    database_path: Path = Path("data/idn_kb.db")
    headless: bool = False
    browser_profile_path: Path = Path("runtime/chrome-profile")
    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load `.env`, allowing real environment variables to take precedence."""
    import os

    load_dotenv(override=False)
    keys = Settings.model_fields.keys()
    values = {key: os.environ[key.upper()] for key in keys if key.upper() in os.environ}
    return Settings.model_validate(values)

