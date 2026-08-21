"""Build and query the small deterministic internal-knowledge index."""
import json,re
from pathlib import Path
from typing import Any

ROOT=Path("data/kb_site_models");OUT=Path("data/resolver")
def load(path:Path):return json.loads(path.read_text(encoding="utf-8"))
def tokens(text:str)->set[str]:return {x for x in re.findall(r"[a-z0-9]+",text.lower()) if len(x)>2}
def _text(obj:Any)->str:
    if isinstance(obj,str):return obj
    if isinstance(obj,list):return " ".join(_text(x) for x in obj)
    if isinstance(obj,dict):return " ".join(_text(v) for k,v in obj.items() if k not in {"raw_text"})
    return str(obj or "")
def build_internal_index()->dict:
    specs=[("POLICY","kb_policies.json","policies"),("FAQ","kb_faq.json","faq"),("TRAINER","kb_trainers.json","trainers"),
           ("CATEGORY_OBSERVATION","kb_category_observations.json","observations"),("LOCATION","kb_locations.json","locations"),("PROMO","kb_promos.json","promos")]
    chunks=[]
    for kind,file,key in specs:
        for i,item in enumerate(load(ROOT/file).get(key,[])):
            title=item.get("name") or item.get("question") or item.get("product") or f"{kind} {i+1}";text=_text(item)
            chunks.append({"id":f"{kind.lower()}:{i+1}","type":kind,"title":title,"text":text,"source":item.get("url") or item.get("source_url") or file,"keywords":sorted(tokens(title+" "+text))})
    for path in sorted((ROOT/"kb_product_snapshots").glob("*.json")):
        item=load(path);chunks.append({"id":f"existing_product:{path.stem}","type":"EXISTING_PRODUCT","title":item.get("name",path.stem),"text":_text(item),"source":item.get("url",str(path)),"keywords":sorted(tokens(_text(item)))})
    result={"schema_version":"internal-knowledge-v1","chunks":chunks,"count":len(chunks)};OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"internal_knowledge_index.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");return result
def retrieve(index:dict,query:str,kinds:set[str]|None=None,limit:int=5)->list[dict]:
    q=tokens(query);ranked=[]
    for chunk in index["chunks"]:
        if kinds and chunk["type"] not in kinds:continue
        score=len(q&set(chunk["keywords"]))
        if score:ranked.append((score,chunk))
    return [x for _,x in sorted(ranked,key=lambda x:(-x[0],x[1]["id"]))[:limit]]
