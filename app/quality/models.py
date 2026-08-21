from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PublishReadiness(str, Enum):
    READY = "READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


class QualityIssue(BaseModel):
    code: str
    field: str | None = None
    message: str
    blocking: bool = False
    value: Any = None


class QualityReport(BaseModel):
    schema_version: str = "publish-quality-v1"
    slug: str
    product_status: str
    completion: float
    publish_readiness: PublishReadiness
    score: int = Field(ge=0, le=100)
    checks: dict[str, Any]
    errors: list[QualityIssue] = Field(default_factory=list)
    warnings: list[QualityIssue] = Field(default_factory=list)

