"""Validated contracts for deterministic knowledge fusion and future publishing."""
from enum import Enum
from typing import Any, Generic, TypeVar
from pydantic import BaseModel, Field

class ResolutionStatus(str,Enum):
    RESOLVED="RESOLVED";NOT_APPLICABLE="NOT_APPLICABLE";REVIEW_REQUIRED="REVIEW_REQUIRED";UNRESOLVED="UNRESOLVED"
class ResolutionMethod(str,Enum):
    DIRECT_FACT="DIRECT_FACT";INTERNAL_KB="INTERNAL_KB";OFFICIAL_RESEARCH="OFFICIAL_RESEARCH";TRUSTED_RESEARCH="TRUSTED_RESEARCH";LOCAL_INFERENCE="LOCAL_INFERENCE";DERIVED="DERIVED";SAFE_DEFAULT="SAFE_DEFAULT"
class ProductStatus(str,Enum):
    PENDING="PENDING";RESOLVING="RESOLVING";RESEARCHING="RESEARCHING";RESOLVED="RESOLVED";REVIEW_REQUIRED="REVIEW_REQUIRED";FAILED="FAILED"

class SourceRef(BaseModel):
    url:str;type:str;title:str|None=None
T=TypeVar("T")
class ResolvedValue(BaseModel,Generic[T]):
    status:ResolutionStatus;value:T|None=None;confidence:float=Field(ge=0,le=1);source_type:str
    sources:list[SourceRef]=Field(default_factory=list);method:ResolutionMethod;needs_review:bool=False
    note:str|None=None;conflict:bool=False;alternatives:list[Any]=Field(default_factory=list)

class AdvertisingLink(BaseModel): url:str;label:str|None=None
class TrainingFormat(BaseModel):
    format:str;duration:str|None=None;schedule:str|None=None;public_price_reference:int|None=None;private_price_reference:int|None=None;price_note:str|None=None
class TargetAudience(BaseModel): audience:str;problem_solved:str|None=None
class Certification(BaseModel):
    name:str;level:str|None=None;exam_fee_reference:int|None=None;exam_duration:str|None=None;exam_code:str|None=None;passing_score:str|None=None
    relationship:str="RELATED";notes:str|None=None
class Tool(BaseModel): name:str;provided_by:str="UNKNOWN"
class NextClass(BaseModel): training_name:str;canonical_source_url:str;reason:str;confidence:float
class TrainerReference(BaseModel): trainer_name:str;kb_trainer_id:str;match_type:str="EXACT"

class KBProductPayload(BaseModel):
    full_name:str;short_name:str|None;category:str;seo_url:str
    advertising_links:list[AdvertisingLink]=Field(default_factory=list);training_formats:list[TrainingFormat]=Field(default_factory=list)
    target_audiences:list[TargetAudience]=Field(default_factory=list);certifications:list[Certification]=Field(default_factory=list)
    tools:list[Tool]=Field(default_factory=list);next_classes:list[NextClass]=Field(default_factory=list)
    trainer_references:list[TrainerReference]=Field(default_factory=list);short_description:str
    learning_outcomes:list[str]=Field(default_factory=list);prerequisites:list[str]=Field(default_factory=list);repeat_policy:str
    practice_examples:list[str]=Field(default_factory=list);post_training_support:str;selling_points:list[str]=Field(default_factory=list)
    claims_to_avoid:list[str]=Field(default_factory=list);additional_notes:str="";active:bool=True

class ResolvedProduct(BaseModel):
    schema_version:str="resolver-v1";slug:str;source_url:str;product_status:ProductStatus
    completion:float;needs_review:bool;fields:dict[str,ResolvedValue[Any]];payload:KBProductPayload
    warnings:list[str]=Field(default_factory=list);source_conflicts:list[dict[str,Any]]=Field(default_factory=list)

class ResearchResult(BaseModel):
    query:str;field:str;answer:Any;source_urls:list[str]=Field(default_factory=list);source_titles:list[str]=Field(default_factory=list)
    source_authority:str;retrieved_at:str;confidence:float
