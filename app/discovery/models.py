"""Typed records produced by IDN discovery."""

from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl


class TrainingProduct(BaseModel):
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    source_url: str
    canonical_url: str
    original_url: str
    source_page: str
    discovered_at: datetime
    status: str = "DISCOVERED"
    potential_duplicate: bool = False


class TrainingCategory(BaseModel):
    name: str
    products: list[TrainingProduct] = Field(default_factory=list)


class TrainingCatalog(BaseModel):
    source: str
    generated_at: datetime
    statistics: dict[str, int]
    categories: list[TrainingCategory]

