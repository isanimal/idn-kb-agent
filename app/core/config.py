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
    crawl_timeout_seconds: float = 30.0
    crawl_delay_seconds: float = 0.75
    crawl_max_retries: int = 3
    crawl_concurrency: int = 2
    crawler_user_agent: str = "IDN-KB-Agent/0.2 (+local reconnaissance; respectful crawler)"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4"
    openai_web_search_enabled: bool = True
    research_max_searches_per_product: int = 3
    research_max_retries: int = 3
    research_concurrency: int = 2
    research_cache_days: int = 30
    ollama_enabled: bool = True
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    ollama_timeout_seconds: float = 180
    ollama_max_retries: int = 3
    ollama_concurrency: int = 1
    ollama_context_size: int = Field(default=4096,ge=512,le=8192)
    ollama_temperature: float = Field(default=.2,ge=0,le=1)
    output_language: str = "id-ID"
    browser_research_concurrency: int = 1
    http_fetch_concurrency: int = 2


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load `.env`, allowing real environment variables to take precedence."""
    import os

    load_dotenv(override=False)
    keys = Settings.model_fields.keys()
    values = {key: os.environ[key.upper()] for key in keys if key.upper() in os.environ}
    return Settings.model_validate(values)
