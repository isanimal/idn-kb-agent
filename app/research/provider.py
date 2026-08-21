"""Optional, cached OpenAI Responses API research provider."""
import hashlib,json,time
from abc import ABC,abstractmethod
from datetime import datetime,timezone,timedelta
from pathlib import Path
from app.resolver.models import ResearchResult

class ResearchProvider(ABC):
    @abstractmethod
    def research(self,*,product:str,field:str,query:str,input_hash:str)->ResearchResult:...

class ResearchCache:
    def __init__(self,root=Path("data/research_cache"),ttl_days=30):self.root=Path(root);self.ttl=timedelta(days=ttl_days);self.hits=0
    def key(self,product,field,query,input_hash):return hashlib.sha256("|".join((product,field,query,input_hash)).encode()).hexdigest()
    def get(self,key):
        p=self.root/f"{key}.json"
        if not p.exists() or datetime.now(timezone.utc)-datetime.fromtimestamp(p.stat().st_mtime,timezone.utc)>self.ttl:return None
        self.hits+=1;return ResearchResult.model_validate_json(p.read_text(encoding="utf-8"))
    def put(self,key,value):self.root.mkdir(parents=True,exist_ok=True);p=self.root/f"{key}.json";p.write_text(value.model_dump_json(indent=2)+"\n",encoding="utf-8");return p

class OpenAIResearchProvider(ResearchProvider):
    def __init__(self,settings,client=None,cache=None):
        if not settings.openai_api_key:raise RuntimeError("OPENAI_API_KEY is not configured")
        if client is None:
            from openai import OpenAI
            client=OpenAI(api_key=settings.openai_api_key)
        self.client=client;self.settings=settings;self.cache=cache or ResearchCache(ttl_days=settings.research_cache_days);self.calls=0;self.failures=0
    def research(self,*,product,field,query,input_hash):
        key=self.cache.key(product,field,query,input_hash);cached=self.cache.get(key)
        if cached:return cached
        last=None
        for attempt in range(self.settings.research_max_retries):
            try:
                self.calls+=1;response=self.client.responses.parse(model=self.settings.openai_model,
                    instructions="Research only the requested unresolved field. Prefer IDN official sources, then official vendor sources. Return concise facts and source URLs. Never invent price, trainer, policy, exam inclusion, or URLs.",
                    input=f"Product: {product}\nField: {field}\nQuery: {query}",tools=[{"type":"web_search"}] if self.settings.openai_web_search_enabled else [],
                    max_tool_calls=self.settings.research_max_searches_per_product,text_format=ResearchResult)
                value=response.output_parsed;self.cache.put(key,value);return value
            except Exception as exc:
                last=exc;self.failures+=1
                if attempt+1<self.settings.research_max_retries:time.sleep(min(4,2**attempt))
        raise RuntimeError(f"Research failed after bounded retries: {last}")
