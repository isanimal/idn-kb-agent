"""Local semantic enrichment providers. Commercial and operational fields are out of scope."""
import hashlib,json,re,time
from abc import ABC,abstractmethod
from datetime import datetime,timezone
from pathlib import Path
from typing import Any,Annotated
import httpx
from pydantic import BaseModel,ConfigDict,Field

PROMPTS=Path("prompts/resolver");CACHE=Path("data/inference_cache");PROMPT_VERSION="ollama-enrichment-v2"
ShortText=Annotated[str,Field(max_length=350)]
class EnrichedAudience(BaseModel):
    model_config=ConfigDict(extra="forbid")
    audience:ShortText;problem_solved:ShortText|None=None
class SemanticEnrichment(BaseModel):
    model_config=ConfigDict(extra="forbid")
    short_description:ShortText
    learning_outcomes:list[ShortText]=Field(default_factory=list,max_length=5)
    target_audiences:list[EnrichedAudience]=Field(default_factory=list,max_length=3)
    prerequisites:list[ShortText]=Field(default_factory=list,max_length=4)
    practice_examples:list[ShortText]=Field(default_factory=list,max_length=4)
    selling_points:list[ShortText]=Field(default_factory=list,max_length=5)
    claims_to_avoid:list[ShortText]=Field(default_factory=list,max_length=5)

class InferenceProvider(ABC):
    provider_name="ABSTRACT"
    @abstractmethod
    def enrich(self,input_data:dict)->tuple[SemanticEnrichment,dict]:...

def extract_json_object(text:str)->dict:
    """Return the first balanced JSON object, ignoring thinking/prose wrappers."""
    decoder=json.JSONDecoder()
    for match in re.finditer(r"\{",text):
        try:value,_=decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:continue
        if isinstance(value,dict):return value
    raise ValueError("No valid JSON object in inference response")

TECHNICAL={"arduino","esp8266","iot","api","bgp","ospf","vlan","nmap","burp","docker","kubernetes","firewall","routing","switching","workflow","llm","rag","wlan","linux","python","n8n"}
ID_WORDS={"dan","yang","untuk","dengan","dari","peserta","mampu","memahami","menggunakan","membuat","pelatihan","training","pada","serta","tidak","dapat","dasar","praktik","sistem"}
EN_WORDS={"the","and","with","from","participants","learn","understand","using","build","create","course","will","skills","system"}
def indonesian_language_ok(value:SemanticEnrichment)->bool:
    text=" ".join([value.short_description,*value.learning_outcomes,*value.prerequisites,*value.practice_examples,*value.selling_points,*value.claims_to_avoid,*[str(x.audience or "")+" "+str(x.problem_solved or "") for x in value.target_audiences]]).lower()
    words=re.findall(r"[a-z]+",text);content=[x for x in words if x not in TECHNICAL]
    return sum(x in ID_WORDS for x in content)>=max(2,sum(x in EN_WORDS for x in content))
def semantic_field_errors(value:SemanticEnrichment)->set[str]:
    errors=set();verbs=("mampu ","memahami ","menggunakan ","membuat ","mengintegrasikan ","mengelola ","menjelaskan ","mengidentifikasi ","mengolah ","membangun ","menerapkan ","melakukan ")
    if value.learning_outcomes and sum(x.lower().startswith(verbs) for x in value.learning_outcomes)<max(1,len(value.learning_outcomes)//2):errors.add("learning_outcomes")
    if any("?" in x for x in value.selling_points):errors.add("selling_points")
    return errors

class RuleBasedInferenceProvider(InferenceProvider):
    provider_name="RULE_BASED"
    def enrich(self,input_data):
        p=input_data["current_payload"];result=SemanticEnrichment(short_description=p["short_description"][:350],learning_outcomes=p["learning_outcomes"][:5],target_audiences=p["target_audiences"][:3],prerequisites=p["prerequisites"][:4],practice_examples=p["practice_examples"][:4],selling_points=p["selling_points"][:5],claims_to_avoid=p["claims_to_avoid"][:5])
        return result,{"provider":self.provider_name,"model":None,"prompt_version":"rule-v1","created_at":datetime.now(timezone.utc).isoformat(),"fallback":True}

class OllamaInferenceProvider(InferenceProvider):
    provider_name="OLLAMA"
    def __init__(self,settings,client=None,cache_root=CACHE):self.s=settings;self.client=client or httpx.Client(timeout=settings.ollama_timeout_seconds);self.cache_root=Path(cache_root);self.calls=0;self.cache_hits=0
    def _key(self,input_data):
        raw=json.dumps(input_data,ensure_ascii=False,sort_keys=True);return hashlib.sha256(f"{self.s.ollama_model}|{PROMPT_VERSION}|{raw}".encode()).hexdigest(),hashlib.sha256(raw.encode()).hexdigest()
    def enrich(self,input_data):
        key,input_hash=self._key(input_data);path=self.cache_root/f"{key}.json"
        if path.exists():
            data=json.loads(path.read_text(encoding="utf-8"));self.cache_hits+=1;return SemanticEnrichment.model_validate(data["output"]),data["metadata"]|{"cache_hit":True}
        system=(PROMPTS/"enrichment_system.txt").read_text(encoding="utf-8");template=(PROMPTS/"enrichment_product.txt").read_text(encoding="utf-8");last=None
        schema=SemanticEnrichment.model_json_schema()
        for attempt in range(self.s.ollama_max_retries):
            try:
                self.calls+=1;r=self.client.post(self.s.ollama_base_url.rstrip("/")+"/api/chat",json={"model":self.s.ollama_model,"stream":False,"think":False,"format":schema,"messages":[{"role":"system","content":system},{"role":"user","content":template.replace("{{INPUT_JSON}}",json.dumps(input_data,ensure_ascii=False,separators=(",",":")))}],"options":{"num_ctx":min(self.s.ollama_context_size,8192),"temperature":self.s.ollama_temperature,"num_predict":600}});r.raise_for_status();body=r.json();raw=body["message"]["content"]
                output=SemanticEnrichment.model_validate(extract_json_object(raw))
                if not indonesian_language_ok(output):raise ValueError("OUTPUT_LANGUAGE_MISMATCH")
                meta={"provider":"OLLAMA","model":self.s.ollama_model,"prompt_version":PROMPT_VERSION,"created_at":datetime.now(timezone.utc).isoformat(),"input_hash":input_hash,"attempts":attempt+1,"cache_hit":False,"inference_seconds":round(body.get("total_duration",0)/1_000_000_000,3) if body.get("total_duration") else None}
                self.cache_root.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps({"metadata":meta,"output":output.model_dump()},ensure_ascii=False,indent=2)+"\n",encoding="utf-8");return output,meta
            except Exception as exc:
                last=exc
                if attempt+1<self.s.ollama_max_retries:time.sleep(min(4,2**attempt))
        raise RuntimeError(f"Ollama inference failed after bounded retries: {last}")

def ollama_status(settings,client=None)->dict:
    result={"runtime":False,"endpoint":settings.ollama_base_url,"configured_model":settings.ollama_model or None,"installed_models":[],"model_installed":False,"context":settings.ollama_context_size,"temperature":settings.ollama_temperature}
    try:
        c=client or httpx.Client(timeout=5);r=c.get(settings.ollama_base_url.rstrip("/")+"/api/tags");r.raise_for_status();result["runtime"]=True;result["installed_models"]=[x.get("name") or x.get("model") for x in r.json().get("models",[])];result["model_installed"]=bool(settings.ollama_model and settings.ollama_model in result["installed_models"])
    except Exception as exc:result["error"]=str(exc)
    return result

def select_inference_provider(settings):
    status=ollama_status(settings)
    return (OllamaInferenceProvider(settings),status) if settings.ollama_enabled and status["runtime"] and status["model_installed"] else (RuleBasedInferenceProvider(),status)
