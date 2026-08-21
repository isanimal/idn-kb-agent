"""Canonical, evidence-bearing records for deterministic training extraction."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FieldStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    PARSE_ERROR = "PARSE_ERROR"


class Evidence(BaseModel):
    source_url: str
    source_section: str
    source_text: str
    confidence: float = 1.0


class FactField(BaseModel):
    status: FieldStatus = FieldStatus.NOT_FOUND
    value: Any = None
    values: list[Any] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class RawSection(BaseModel):
    heading: str
    semantic_type: str
    content: str = ""
    items: list[Any] = Field(default_factory=list)


class TrainingFacts(BaseModel):
    identity: dict[str, FactField]
    description: FactField = Field(default_factory=FactField)
    duration: FactField = Field(default_factory=FactField)
    price: FactField = Field(default_factory=FactField)
    training_format: FactField = Field(default_factory=FactField)
    curriculum: FactField = Field(default_factory=FactField)
    benefits: FactField = Field(default_factory=FactField)
    facilities: FactField = Field(default_factory=FactField)
    prerequisites: FactField = Field(default_factory=FactField)
    target_audiences: FactField = Field(default_factory=FactField)
    certifications: FactField = Field(default_factory=FactField)
    trainers: FactField = Field(default_factory=FactField)
    tools: FactField = Field(default_factory=FactField)
    practice: FactField = Field(default_factory=FactField)
    support_information: FactField = Field(default_factory=FactField)
    repeat_policy: FactField = Field(default_factory=FactField)
    related_training: FactField = Field(default_factory=FactField)
    contact_information: FactField = Field(default_factory=FactField)
    raw_sections: list[RawSection] = Field(default_factory=list)
    unknown_sections: list[RawSection] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    completeness: dict[str, float | int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionMetadata(BaseModel):
    training_source_id: int
    canonical_url: str
    status: str
    fetch_method: str
    http_status: int | None = None
    content_hash: str
    template_type: str
    extracted_at: datetime
    facts_path: str
    evidence_path: str
    raw_snapshot_path: str
    last_error: str | None = None
