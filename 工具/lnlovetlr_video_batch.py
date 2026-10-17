"""Lnlovetlr async-video adapter; one create request per task ledger."""
from pathlib import Path
from urllib import request,error,parse
from datetime import datetime,timezone
import argparse,json,hashlib,os,re,sys,uuid,shutil
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/"工具"))
import dolasd_video_batch as media

def now():return datetime.now(timezone.utc).isoformat()
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(temp,path)
def append(path,value):
    with Path(path).open("a",encoding="utf-8",newline="\n") as f:
        f.write(json.dumps({"at":now(),**value},ensure_ascii=False)+"\n");f.flush();os.fsync(f.fileno())
def latest(path):
    value={}
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():value.update(json.loads(line))
    return value
class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
class Client:
    def __init__(self,provider_id):
        cfg=json.loads((ROOT/"工具/api配置.json").read_text(encoding="utf-8-sig"))
        self.cfg=cfg["providers"][provider_id];self.base=self.cfg["base_url"].rstrip("/")
        self.key=re.search(r"KEY:\s*(\S+)",Path(self.cfg["credential_file"]).read_text(encoding="utf-8-sig")).group(1)
        self.opener=request.build_opener(NoRedirect)
    def clean(self,value):return str(value).replace(self.key,"[REDACTED]")
    def call(self,method,url,body=None,mime=None,auth=False,timeout=60):
        headers={"Accept":"application/json","User-Agent":"ShortDramaVideo/1.0"}
        if auth:
            if parse.urlsplit(url).netloc not in ["api.lnlovetlr.cloud","video.lnlovetlr.cloud"]:raise RuntimeError("Credential destination differs")
            headers["Authorization"]="Bearer "+self.key
        if mime:headers["Content-Type"]=mime
        if method=="POST" and mime and mime.startswith("image/"):headers["X-Reference-Kind"]="image"
        try:
            with self.opener.open(request.Request(url,data=body,headers=headers,method=method),timeout=timeout) as resp:status=resp.status;raw=resp.read(3_000_000)
        except error.HTTPError as exc:status=exc.code;raw=exc.read(3_000_000);exc.close()
        text=self.clean(raw.decode("utf-8","replace"))
        try:value=json.loads(text)
        except ValueError:value={"message":text[:1000]}
        return status,value
    def check(self,model,duration,ratio,resolution,folder):
        http,data=self.call("GET",self.base+"/v1/models",auth=True)
        if http!=200:raise RuntimeError("Permission query HTTP "+str(http)+": "+self.clean(data)[:500])
        if model not in [r.get("id") for r in data.get("data",[])]:raise RuntimeError("Requested model is absent from Key permissions")
        http,catalog=self.call("GET",self.cfg["catalog_url"])
        if http!=200:raise RuntimeError("Live public catalog HTTP "+str(http))
        m=next((r for r in catalog.get("models",[]) if r.get("id")==model),None)
        if not m:raise RuntimeError("Requested model is not currently published")
        p=m["parameters"]
        if duration not in p["duration"] or ratio not in p["aspect_ratio"] or resolution not in p["resolution"]:raise RuntimeError("Requested duration/ratio/resolution is unsupported")
        save(folder/"model-preflight.json",{"at":now(),"permission_confirmed":True,"model":m})
        return m
def prepare(client,job,folder,model):
    prompt=Path(job["prompt_file"]).read_text(encoding="utf-8-sig").strip()
    limit=model["parameters"].get("prompt_max_length")
    if limit and len(prompt.encode("utf-16-le"))//2>limit:raise RuntimeError("Prompt exceeds actual model limit")
    refs=media.read_references(job["references"])
    if len(refs)>min(9,model["parameters"]["max_reference_images"]):raise RuntimeError("Reference count exceeds limit")
    uploaded_path=folder/"uploads.json"
    uploaded=json.loads(uploaded_path.read_text(encoding="utf-8")) if uploaded_path.exists() else {}
    urls=[]
    for ref in refs:
        digest=ref["sha256"]
        if digest not in uploaded:
            http,data=client.call("POST",client.cfg["reference_upload_url"],body=ref["content"],mime=ref["mime"],auth=True,timeout=120)
            if http!=200 or data.get("success") is not True or not data.get("url"):raise RuntimeError("Reference upload HTTP "+str(http)+": "+client.clean(data)[:500])
            if parse.urlsplit(data["url"]).scheme!="https":raise RuntimeError("Reference URL is not HTTPS")
            uploaded[digest]={"path":ref["path"],"sha256":digest,"url":data["url"],"receipt":data};save(uploaded_path,uploaded)
            print(json.dumps({"uploaded":Path(ref["path"]).name,"bytes":ref["bytes"]},ensure_ascii=False),flush=True)
        urls.append(uploaded[digest]["url"])
    payload=dict(model["example"])
    payload.update(model=job["model"],prompt=prompt,duration=job["duration"],ratio=job["ratio"],resolution=job["resolution"],images=urls,video_urls=[],audio_urls=[])
    raw=json.dumps(payload,ensure_ascii=False).encode("utf-8")
    (folder/"request.json").write_bytes(raw)
    metadata={k:v for k,v in job.items() if k!="references"}
    metadata.update(reference_metadata=[{k:v for k,v in r.items() if k!="content"} for r in refs],request_sha256=hashlib.sha256(raw).hexdigest(),prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),catalog_estimate_cny=model.get("price_cny"))
    save(folder/"request-metadata.json",metadata)
    return raw,metadata
def submit(client,job,folder,ledger):
    if ledger.exists():
        print(json.dumps({"existing":True,**latest(ledger)},ensure_ascii=False));return
    m=client.check(job["model"],job["duration"],job["ratio"],job["resolution"],folder)
    raw,meta=prepare(client,job,folder,m)
    append(ledger,{"unit":job["unit"],"provider":job["provider"],"model":job["model"],"status":"submitting","duration":job["duration"],"ratio":job["ratio"],"resolution":job["resolution"],"request_sha256":meta["request_sha256"],"catalog_estimate_cny":meta["catalog_estimate_cny"]})
    try:http,response=client.call("POST",client.base+"/v1/videos",body=raw,mime="application/json",auth=True,timeout=600)
    except Exception as exc:
        append(ledger,{"status":"unknown_outcome","error":client.clean(exc)});raise RuntimeError("Create result unknown; original request retained") from None
    save(folder/"create-response.json",response)
    task=response.get("data",response) if isinstance(response,dict) else {}
    identity=(task.get("id") or task.get("task_id")) if isinstance(task,dict) else None
    if http not in (200,201,202) or not identity:
        state="submit_rejected" if 400<=http<500 or response.get("success") is False else "unknown_outcome"
        append(ledger,{"status":state,"http":http,"error":client.clean(response)[:1200]})
        raise RuntimeError("Create HTTP "+str(http)+": "+client.clean(response)[:500])
    append(ledger,{"task_id":str(identity),"status":task.get("status","submitted"),"http":http})
    print(json.dumps({"unit":job["unit"],"task_id":identity,"status":task.get("status","submitted"),"catalog_estimate_cny":meta["catalog_estimate_cny"]},ensure_ascii=False),flush=True)
def download(client,url,target):
    for _ in range(5):
        parsed=parse.urlsplit(url)
        if parsed.scheme!="https":raise RuntimeError("Artifact URL is not HTTPS")
        headers={"User-Agent":"ShortDramaVideo/1.0"}
        if parsed.netloc==parse.urlsplit(client.base).netloc:headers["Authorization"]="Bearer "+client.key
        try:response=client.opener.open(request.Request(url,headers=headers),timeout=180)
        except error.HTTPError as exc:response=exc
        with response:
            if response.status in (301,302,303,307,308):
                location=response.headers.get("Location")
                if not location:raise RuntimeError("Redirect missing Location")
                url=parse.urljoin(url,location);continue
            if response.status!=200:raise RuntimeError("Download HTTP "+str(response.status))
            if "json" in response.headers.get("Content-Type",""):raise RuntimeError("Artifact endpoint returned JSON")
            with target.open("xb") as f:shutil.copyfileobj(response,f,1024*1024)
            return
    raise RuntimeError("Too many artifact redirects")
def poll(client,job,folder,ledger):
    state=latest(ledger)
    if not state.get("task_id"):raise RuntimeError("No task ID; retain request for reconciliation")
    if state.get("status") in ["succeeded","failed","FAILURE"]:print(json.dumps(state,ensure_ascii=False));return
    identity=state["task_id"]
    http,data=client.call("GET",client.base+"/v1/videos/"+parse.quote(identity,safe=""),auth=True)
    if http!=200:raise RuntimeError("Status HTTP "+str(http)+": "+client.clean(data)[:400])
    save(folder/"last-response.json",data);task=data.get("data",data);remote=task.get("status","unknown")
    append(ledger,{"status":remote,"progress":task.get("progress")})
    if remote in ["failed","FAILURE"]:
        append(ledger,{"error":client.clean(task.get("fail_reason") or task.get("error") or "failed")});print(json.dumps(latest(ledger),ensure_ascii=False));return
    if remote not in ["completed","SUCCESS"]:
        print(json.dumps({"task_id":identity,"status":remote,"progress":task.get("progress")},ensure_ascii=False));return
    out=Path(job["output_dir"]);out.mkdir(parents=True,exist_ok=True)
    target=out/f'{job["unit"]:02d}单元-lnlovetlr-{identity}.mp4'
    if target.exists():
        report=media.verify(target,job["duration"],"720x1280")
    else:
        http,artifact_list=client.call("GET",client.base+"/v1/tasks/"+parse.quote(identity,safe="")+"/artifacts",auth=True)
        if http!=200:raise RuntimeError("Artifact list HTTP "+str(http))
        artifacts=artifact_list.get("data",artifact_list).get("artifacts",[])
        artifact=next((a for a in artifacts if a.get("type") in ["video","mp4"] or a.get("key")=="video"),None)
        if not artifact or not artifact.get("content_url"):raise RuntimeError("No published video artifact")
        temp=out/("lnlovetlr-"+identity+"."+uuid.uuid4().hex+".part")
        download(client,parse.urljoin(client.base,artifact["content_url"]),temp)
        report=media.verify(temp,job["duration"],"720x1280")
        temp.rename(target)
    report.update(task_id=identity,status="succeeded",output_path=str(target))
    save(folder/"technical-report.json",report);append(ledger,report)
    print(json.dumps(report,ensure_ascii=False),flush=True)
def main():
    parser=argparse.ArgumentParser();parser.add_argument("mode",choices=["check","submit","poll"]);parser.add_argument("--job",required=True);args=parser.parse_args()
    job=json.loads(Path(args.job).read_text(encoding="utf-8-sig"));folder=Path(job["task_dir"]);folder.mkdir(parents=True,exist_ok=True);ledger=folder/"lnlovetlr-tasks.jsonl"
    client=Client(job["provider"])
    if args.mode=="check":print(json.dumps(client.check(job["model"],job["duration"],job["ratio"],job["resolution"],folder),ensure_ascii=False))
    elif args.mode=="submit":submit(client,job,folder,ledger)
    else:poll(client,job,folder,ledger)
if __name__=="__main__":main()
