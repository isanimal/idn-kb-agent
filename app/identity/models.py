from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class IdentityDecision(str,Enum):
    CREATE_NEW="CREATE_NEW";UPDATE_EXISTING="UPDATE_EXISTING";REVIEW_REQUIRED="REVIEW_REQUIRED"

class MatchMethod(str,Enum):
    EXACT_CANONICAL_URL="EXACT_CANONICAL_URL";EXACT_NORMALIZED_NAME="EXACT_NORMALIZED_NAME";EXPLICIT_KB_ID_MAPPING="EXPLICIT_KB_ID_MAPPING";ALIAS_CANDIDATE="ALIAS_CANDIDATE";FUZZY_CANDIDATE="FUZZY_CANDIDATE";NO_CANDIDATE="NO_CANDIDATE"

class Candidate(BaseModel):
    kb_product_id:str;name:str;detail_url:str;score:float;reasons:list[str]=Field(default_factory=list)

class IdentityResult(BaseModel):
    source_slug:str;source_name:str;source_url:str;decision:IdentityDecision;confidence:float;match_method:MatchMethod
    existing_product:dict[str,Any]|None=None;candidate_matches:list[Candidate]=Field(default_factory=list);warnings:list[str]=Field(default_factory=list)

