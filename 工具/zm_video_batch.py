from __future__ import annotations
from pathlib import Path
from urllib import request,error,parse
import argparse,json,hashlib,re,os,sys,uuid,time,shutil
from datetime import datetime,timezone
from video_provider_catalog import read_config,token_for
PROVIDER="zm_video";ROOT=Path(__file__).resolve().parent.parent
class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
def now():return datetime.now(timezone.utc).isoformat()
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8");os.replace(tmp,path)
def append(path,row):
    with path.open("a",encoding="utf-8",newline="\n") as h:
        h.write(json.dumps({"at":now(),"provider":PROVIDER,**row},ensure_ascii=False)+"\n");h.flush();os.fsync(h.fileno())
def state(path):
    result={}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():result.update(json.loads(line))
    return result
class Client:
    def __init__(self):
        self.p=read_config()["providers"][PROVIDER];self.base=self.p["base_url"].rstrip("/")
        self.key=token_for(self.p);self.opener=request.build_opener(NoRedirect)
        if not self.key:raise RuntimeError("ZM credential missing")
    def clean(self,x):return str(x).replace(self.key,"[REDACTED]")
    def call(self,method,path,payload=None,timeout=60):
        raw=json.dumps(payload,ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers={"Authorization":"Bearer "+self.key,"Accept":"application/json","User-Agent":"ShortDramaVideo/1.0"}
        if raw:headers["Content-Type"]="application/json"
        try:
            with self.opener.open(request.Request(self.base+path,data=raw,headers=headers,method=method),timeout=timeout) as r:status=r.code;content=r.read(3000000).decode("utf-8","replace")
        except error.HTTPError as e:status=e.code;content=e.read().decode("utf-8","replace");e.close()
        try:reply=json.loads(self.clean(content))
        except ValueError:reply={"message":self.clean(content)[:700]}
        return status,reply
def check(c,model,duration):
    status,reply=c.call("GET","/v1/models")
    if status!=200 or model not in [r.get("id") for r in reply.get("data",[])]:raise RuntimeError("ZM requested model unavailable")
    if not 4<=duration<=30:raise RuntimeError("Duration outside documented video range")
    pricing_req=request.Request("https://model.aihubkit.com/api/pricing",headers={"Accept":"application/json","User-Agent":"ShortDramaVideo/1.0"})
    with c.opener.open(pricing_req,timeout=35) as pricing_response:pricing=json.load(pricing_response)
    row=next((r for r in pricing.get("data",[]) if r.get("model_name")==model),None)
    if not row:raise RuntimeError("Requested model absent from current ZM pricing catalogue")
    tiers=re.findall(r'tier\("([^\"]+)",\s*usd\(([0-9.]+)\s*\*\s*seconds',row.get("billing_expr",""))
    rate=next((float(value) for tier,value in tiers if "720p" in tier.lower() and "有参考视频" not in tier),None)
    estimate=rate*duration if rate is not None else None
    http,balance=c.call("GET","/api/user/self_v2")
    data=balance.get("data",{})
    return {"at":now(),"provider":PROVIDER,"model":model,"duration":duration,"resolution":"720p","ratio":"9:16","quota_usd":data.get("quota")/500000 if http==200 and balance.get("success") and isinstance(data.get("quota"),(int,float)) else None,"estimate_usd":estimate,"estimate_cny":estimate*7.3 if estimate is not None else None,"billing_expr":row.get("billing_expr")}
def upload_refs(refs,task_dir):
    config=read_config();host=config["providers"]["lnlovetlr_video"]
    key=token_for(host);endpoint=host["reference_upload_url"]
    if endpoint!="https://video.lnlovetlr.cloud/api/workbench/reference":raise RuntimeError("Material host endpoint changed")
    opener=request.build_opener(NoRedirect)
    result=[];cache=task_dir/"reference-uploads.json"
    existing=json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    for pos,source in enumerate(refs,1):
        p=Path(source).resolve();blob=p.read_bytes();digest=hashlib.sha256(blob).hexdigest()
        if not blob.startswith(b"\x89PNG\r\n\x1a\n"):raise RuntimeError("This scoped batch requires original PNG references")
        if digest in existing:row=existing[digest]
        else:
            req=request.Request(endpoint,data=blob,headers={"Authorization":"Bearer "+key,"Content-Type":"image/png","X-Reference-Kind":"image","Accept":"application/json","User-Agent":"ShortDramaVideo/1.0"},method="POST")
            try:
                with opener.open(req,timeout=120) as r:status=r.code;raw=r.read(1000000).decode("utf-8","replace")
            except error.HTTPError as e:status=e.code;raw=e.read().decode("utf-8","replace");e.close()
            value=json.loads(raw.replace(key,"[REDACTED]"))
            if status!=200 or value.get("success") is not True or not value.get("url"):raise RuntimeError("Reference hosting failed: "+str(status))
            row={"path":str(p),"sha256":digest,"bytes":len(blob),"url":value["url"]}
            if parse.urlsplit(row["url"]).scheme!="https":raise RuntimeError("Reference URL must be HTTPS")
            existing[digest]=row;save(cache,existing)
        result.append({**row,"position":pos})
    return result
def submit(c,args):
    folder=args.task_dir;folder.mkdir(parents=True,exist_ok=True);ledger=folder/"zm-tasks.jsonl"
    if ledger.exists():raise RuntimeError("Existing creation attempt; reconcile original before POST")
    report=check(c,args.model,args.duration);save(folder/"preflight.json",report)
    if report.get("quota_usd") is not None and report.get("estimate_usd") is not None and report["quota_usd"]<report["estimate_usd"]:raise RuntimeError("Current ZM quota below live estimate")
    refs=upload_refs(args.reference,folder)
    if not 1<=len(refs)<=9:raise RuntimeError("Project requires 1–9 references")
    prompt=args.prompt_file.read_text(encoding="utf-8-sig").strip()
    payload={"model":args.model,"prompt":prompt,"duration":args.duration,"resolution":args.resolution,"ratio":args.ratio,"referenceImages":[r["url"] for r in refs],"referenceAudios":[],"referenceVideos":[]}
    raw=json.dumps(payload,ensure_ascii=False).encode("utf-8")
    (folder/"request.json").write_bytes(raw)
    metadata={"unit":args.unit,"model":args.model,"duration":args.duration,"resolution":args.resolution,"ratio":args.ratio,"prompt_sha256":hashlib.sha256(prompt.encode()).hexdigest(),"request_sha256":hashlib.sha256(raw).hexdigest(),"reference_sha256s":[r["sha256"] for r in refs],"reference_paths":[r["path"] for r in refs],"estimate_usd":report["estimate_usd"]}
    append(ledger,{**metadata,"status":"submitting"})
    try:http,reply=c.call("POST","/v1/videos",payload=payload,timeout=600)
    except Exception as e:
        append(ledger,{"status":"unknown_outcome","error":c.clean(e)});raise
    save(folder/"create-response.json",reply)
    task=reply.get("data",reply) if isinstance(reply,dict) else {}
    identity=task.get("id") or task.get("task_id")
    if http not in (200,201,202) or not identity:
        known=400<=http<500 or reply.get("success") is False
        append(ledger,{"status":"submit_rejected" if known else "unknown_outcome","http":http,"error":c.clean(json.dumps(reply,ensure_ascii=False))[:1000]})
        raise RuntimeError("ZM create response lacked accepted task: "+str(http))
    append(ledger,{"task_id":str(identity),"status":task.get("status","queued"),"http":http})
    print(json.dumps({"task_id":str(identity),"status":task.get("status","queued"),"unit":args.unit},ensure_ascii=False),flush=True)
def download(c,task_id,target):
    url=c.base+"/v1/videos/"+parse.quote(task_id,safe="")+"/content"
    for _ in range(6):
        parsed=parse.urlsplit(url)
        if parsed.scheme!="https":raise RuntimeError("Download returned non-HTTPS")
        headers={"User-Agent":"ShortDramaVideo/1.0"}
        if parsed.netloc==parse.urlsplit(c.base).netloc:headers["Authorization"]="Bearer "+c.key
        try:response=c.opener.open(request.Request(url,headers=headers),timeout=180)
        except error.HTTPError as e:response=e
        with response:
            if response.code in (301,302,303,307,308):
                url=parse.urljoin(url,response.headers["Location"]);continue
            if response.code!=200:raise RuntimeError("Media HTTP "+str(response.code))
            if "json" in response.headers.get("Content-Type",""):
                value=json.load(response);value=value.get("data",value)
                url=value.get("video_url") or value.get("url")
                if not url:raise RuntimeError("Media response missing URL")
                continue
            with target.open("xb") as h:shutil.copyfileobj(response,h,1024*1024)
            return
    raise RuntimeError("Media redirect limit")
def poll(c,args):
    ledger=args.task_dir/"zm-tasks.jsonl";deadline=time.monotonic()+args.timeout
    while True:
        s=state(ledger);identity=s.get("task_id")
        if not identity:raise RuntimeError("Original creation has no task ID")
        if s.get("status")=="downloaded":
            print(json.dumps({"task_id":identity,"status":"downloaded","output_path":s["output_path"]},ensure_ascii=False));return 0
        http,reply=c.call("GET","/v1/videos/"+parse.quote(identity,safe=""))
        if http!=200:raise RuntimeError("ZM query HTTP "+str(http))
        task=reply.get("data",reply);remote=str(task.get("status","unknown")).lower()
        save(args.task_dir/"last-status.json",task)
        if remote!=s.get("status"):append(ledger,{"status":remote,"progress":task.get("progress")})
        print(json.dumps({"task_id":identity,"status":remote,"progress":task.get("progress")},ensure_ascii=False),flush=True)
        if remote in ("completed","success","succeeded"):
            args.output_dir.mkdir(parents=True,exist_ok=True)
            target=args.output_dir/("开场补镜-ZM-"+str(s["duration"])+"秒-"+identity+".mp4")
            if not target.exists():
                temporary=args.output_dir/(identity+".part")
                if temporary.exists():raise RuntimeError("Prior partial file retained; local recovery required")
                download(c,identity,temporary)
                from dolasd_video_batch import verify
                report=verify(temporary,int(s["duration"]),"720x1280");os.replace(temporary,target)
            else:
                from dolasd_video_batch import verify
                report=verify(target,int(s["duration"]),"720x1280")
            save(args.task_dir/"technical-report.json",report|{"output_path":str(target),"task_id":identity})
            append(ledger,{"status":"downloaded","output_path":str(target),"technical_verified":True})
            print(json.dumps({"status":"downloaded","output_path":str(target),**report},ensure_ascii=False),flush=True);return 0
        if remote in ("failed","error","cancelled","canceled"):
            message=task.get("error") or task.get("fail_reason") or task.get("message")
            append(ledger,{"status":remote,"error":c.clean(message)})
            raise RuntimeError("ZM task failed: "+c.clean(message)[:600])
        if not args.watch or time.monotonic()>=deadline:return 3
        time.sleep(args.interval)
def main():
    p=argparse.ArgumentParser();p.add_argument("mode",choices=["check","submit","poll"])
    p.add_argument("--model",default="seedance-2.5_k1");p.add_argument("--duration",type=int,default=30)
    p.add_argument("--resolution",default="720p");p.add_argument("--ratio",default="9:16");p.add_argument("--unit",default="0.5")
    p.add_argument("--prompt-file",type=Path);p.add_argument("--reference",action="append",default=[])
    p.add_argument("--task-dir",type=Path);p.add_argument("--output-dir",type=Path);p.add_argument("--watch",action="store_true")
    p.add_argument("--interval",type=int,default=30);p.add_argument("--timeout",type=int,default=1800)
    a=p.parse_args();c=Client()
    if a.mode=="check":print(json.dumps(check(c,a.model,a.duration),ensure_ascii=False));return 0
    if not a.task_dir:raise RuntimeError("task directory required")
    a.task_dir=a.task_dir.resolve()
    if a.mode=="submit":submit(c,a);return 0
    if not a.output_dir:a.output_dir=a.task_dir
    a.output_dir=a.output_dir.resolve()
    return poll(c,a)
if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception as e:print("ERROR "+str(e)[:1000],flush=True);raise SystemExit(1)
