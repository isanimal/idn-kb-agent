"""Models describing page reconnaissance."""

from datetime import datetime
from pydantic import BaseModel, Field


class HeadingInventoryItem(BaseModel):
    raw_heading: str
    normalized_text: str
    seen_on: list[str] = Field(default_factory=list)


class PageAnalysis(BaseModel):
    url: str
    fetch_method: str
    page_type: str
    title: str = ""
    h1: list[str] = Field(default_factory=list)
    h2: list[str] = Field(default_factory=list)
    h3: list[str] = Field(default_factory=list)
    section_headings: list[str] = Field(default_factory=list)
    json_ld_types: list[str] = Field(default_factory=list)
    list_count: int = 0
    table_count: int = 0
    patterns: dict[str, bool] = Field(default_factory=dict)
    internal_links: list[str] = Field(default_factory=list)


class ReconnaissanceReport(BaseModel):
    generated_at: datetime
    pages_attempted: int = 0
    pages_successful: int = 0
    pages_failed: int = 0
    http_fetched: int = 0
    browser_fetched: int = 0
    categories_found: int = 0
    training_products_found: int = 0
    duplicate_urls_found: int = 0
    sample_landing_pages_analyzed: int = 0
    supporting_pages_found: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)
    duration_seconds: float = 0
