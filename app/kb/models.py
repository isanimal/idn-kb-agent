from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AuthState(str, Enum):
    UNKNOWN = "UNKNOWN"
    AUTHENTICATED = "AUTHENTICATED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    WAITING_FOR_AUTH = "WAITING_FOR_AUTH"
    AUTH_RESTORED = "AUTH_RESTORED"
    AUTH_FAILED = "AUTH_FAILED"


class NavigationItem(BaseModel):
    label: str
    url: str
    parent: str | None = None


class FormField(BaseModel):
    field_key: str
    label: str
    control_type: str
    required: bool = False
    multiple: bool = False
    dynamic: bool = False
    placeholder: str | None = None
    help_text: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)
    preferred_locator: dict[str, str]
    fallbacks: list[dict[str, str]] = Field(default_factory=list)


class KBReconReport(BaseModel):
    generated_at: datetime
    authentication: dict[str, Any] = Field(default_factory=dict)
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    warnings: list[str] = Field(default_factory=list)
