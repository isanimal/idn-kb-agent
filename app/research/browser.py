"""Unpaid, read-only research over known and official web sources."""
import hashlib,json,re
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup

AUTHORITY_PATH=Path("data/resolver/source_authority.json");ROOT=Path("data/research");CACHE=Path("data/research_cache/browser");CACHE_VERSION="official-content-v3"
def domain(url):return (urlparse(url).hostname or "").lower()
class SourceAuthority:
    def __init__(self,path=AUTHORITY_PATH):self.values=json.loads(Path(path).read_text(encoding="utf-8"))
    def classify(self,url):
        host=domain(url)
        if host in self.values:return self.values[host]
        return next((v for d,v in self.values.items() if host.endswith("."+d)),"TRUST_UNKNOWN")

class BrowserResearchProvider:
    """Fetch direct official sources first; no paid search service or mutation."""
    def __init__(self,settings,client=None,authority=None):
        self.s=settings;self.client=client or httpx.Client(timeout=settings.crawl_timeout_seconds,follow_redirects=True,headers={"User-Agent":settings.crawler_user_agent});self.authority=authority or SourceAuthority();self.cache_hits=0;self.fetches=0
    def _cache(self,url,ttl_days):
        key=hashlib.sha256(f"{CACHE_VERSION}|{url}".encode()).hexdigest();path=CACHE/f"{key}.json"
        if path.exists() and datetime.now(timezone.utc)-datetime.fromtimestamp(path.stat().st_mtime,timezone.utc)<timedelta(days=ttl_days):self.cache_hits+=1;return json.loads(path.read_text(encoding="utf-8"))
        self.fetches+=1;r=self.client.get(url);r.raise_for_status();soup=BeautifulSoup(r.text,"html.parser")
        for x in soup.select("script,style,noscript,svg"):x.decompose()
        content=soup.select_one(".betterdocs-content, .entry-content, #content, .elementor, article, main") or soup.body or soup
        value={"url":str(r.url),"title":soup.title.get_text(" ",strip=True) if soup.title else "","text":re.sub(r"\s+"," ",content.get_text(" ",strip=True))}
        CACHE.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False)+"\n",encoding="utf-8");return value
    def research_field(self,slug,product,field,known_urls):
        terms={"training_formats":["durasi","jam training","biaya investasi","harga"]}.get(field,[field.replace("_"," ")]);evidence=[];search=[];fetched=[]
        for url in dict.fromkeys(x for x in known_urls if x):
            authority=self.authority.classify(url)
            if field=="training_formats" and authority not in {"IDN_PRIMARY","KB_INTERNAL"}:continue
            try:page=self._cache(url,7 if authority=="IDN_PRIMARY" else 30);fetched.append({"url":page["url"],"title":page["title"],"authority":authority})
            except Exception as exc:fetched.append({"url":url,"error":str(exc),"authority":authority});continue
            lower=page["text"].lower();positions=[]
            if field=="training_formats":positions.extend(m.start() for m in re.finditer(r"(?:format|metode|pelaksanaan|kelas)\s+(?:training\s+)?(?:adalah\s+|:\s*)?(?:hybrid|offline|online)\b|\b(?:hybrid|offline|online)\s+(?:class|kelas)\b",lower,re.I))
            positions.extend(lower.find(term) for term in terms if lower.find(term)>=0)
            for pos in sorted(positions):
                if pos>=0:
                    snippet=page["text"][max(0,pos-180):pos+420]
                    evidence.append({"field":field,"url":page["url"],"title":page["title"],"domain":domain(page["url"]),"authority":authority,"retrieved_at":datetime.now(timezone.utc).isoformat(),"evidence":snippet});break
        folder=ROOT/slug;folder.mkdir(parents=True,exist_ok=True)
        (folder/"search_results.json").write_text(json.dumps(search,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");(folder/"fetched_sources.json").write_text(json.dumps(fetched,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");result={"product":product,"field":field,"evidence":evidence,"fetches":self.fetches,"cache_hits":self.cache_hits};(folder/"research.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");return result
    def check(self):
        try:r=self.client.get("https://www.idn.id/training/");return {"available":r.status_code<500,"status_code":r.status_code,"provider":"DIRECT_OFFICIAL_HTTP"}
        except Exception as exc:return {"available":False,"error":str(exc),"provider":"DIRECT_OFFICIAL_HTTP"}
