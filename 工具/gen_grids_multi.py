# -*- coding: utf-8 -*-
# Multi-API concurrent image generation with topological layers + cross-provider failover.
# Handles BOTH grid tasks (refs -> edits) and asset tasks (no refs -> text-to-image) for every provider.
# Providers: c2a (chatgpt2api, local deploy, sync, multipart local files -- DEFAULT MAIN CHANNEL,
#            free + no concurrency cap, user ruling 2026-07-16)
#            flow2api_banana2 (Gemini official format, local image-to-image -- COLORED-PENCIL ONLY,
#            forced 9:16 + 1K + fixed prompt, user ruling 2026-08-02)
#            poloai (sync edits, multipart local files) + wuyin (async image_gpt, via imgbb) -- BACKUPS, off by default.
# Each provider has its own concurrency cap (semaphore); tasks round-robin across enabled providers;
# a task that exhausts its primary provider fails over to the others.
# Usage: python gen_grids_multi.py <task_json> [c2a_cap] [poloai_cap] [wuyin_cap]
#   caps default 6/0/0; set a cap to 0 to disable that provider, >0 to enable as failover.
# PREFLIGHT: c2a needs its backend up -- curl http://127.0.0.1:8000/v1/models -H "Authorization: Bearer <key>".
# Colored-pencil tasks preflight Flow2API /models automatically and never fail over to another model.
import json, os, re, sys, shutil, base64, uuid, subprocess, time, threading, mimetypes, tomllib
import urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

TASK_JSON  = sys.argv[1] if len(sys.argv) > 1 else r"<主仓库根>\工具\宫格任务-07-polo.json"
C2A_CAP    = int(sys.argv[2]) if len(sys.argv) > 2 else 6
POLO_CAP   = int(sys.argv[3]) if len(sys.argv) > 3 else 0
WY_CAP     = int(sys.argv[4]) if len(sys.argv) > 4 else 0

TOOLDIR = r"<主仓库根>\工具"
_wyall = None
for fn in os.listdir(TOOLDIR):
    if fn.startswith("api") and fn.endswith(".json"):
        try:
            c = json.load(open(os.path.join(TOOLDIR, fn), encoding="utf-8"))
            if c.get("providers", {}).get("wuyinkeji"):
                _wyall = c; break
        except Exception:
            pass
WY    = _wyall["providers"]["wuyinkeji"] if _wyall else None
IMGBB = _wyall["imgbb"] if _wyall else None
C2A   = (_wyall or {}).get("providers", {}).get("chatgpt2api")
POLO  = (_wyall or {}).get("providers", {}).get("poloai")
FLOW2 = (_wyall or {}).get("providers", {}).get("flow2api_banana2")

PLAN   = json.load(open(TASK_JSON, encoding="utf-8"))
OUTDIR = PLAN["output_dir"]
tasks  = {t["id"]: t for t in PLAN["tasks"]}
TMP    = os.path.join(os.environ["TEMP"], "gridmulti")
os.makedirs(TMP, exist_ok=True); os.makedirs(OUTDIR, exist_ok=True)

def is_flow2_colored_task(t):
    """Colored-pencil derivatives are a dedicated route, never a general-provider failover."""
    if t.get("provider") == "flow2api_banana2":
        return True
    return "彩铅参考图" in os.path.basename(t.get("file", "")) and bool(t.get("refs"))

for _tid, _task in tasks.items():
    if "彩铅参考图" in os.path.basename(_task.get("file", "")) and not _task.get("refs"):
        print("colored-pencil task requires exactly one source ref: " + _tid)
        sys.exit(1)

FLOW_TASK_IDS = [tid for tid, t in tasks.items() if is_flow2_colored_task(t)]
REGULAR_TASK_IDS = [tid for tid, t in tasks.items() if not is_flow2_colored_task(t)]

POLO_EP = POLO["base_url"].rstrip("/") + "/v1/images/edits" if POLO else ""
if WY:
    WY_BASE   = WY["base_url"].rstrip("/")
    WY_SUBMIT = WY_BASE + "/api/async/image_gpt"
    WY_DETAIL = WY_BASE + "/api/async/detail"
    IMGBB_URL = "https://api.imgbb.com/1/upload?key=" + IMGBB["api_key"]

if C2A:
    C2A_BASE = C2A["base_url"].rstrip("/")
    C2A_GEN  = C2A_BASE + "/v1/images/generations"
    C2A_EDIT = C2A_BASE + "/v1/images/edits"

if FLOW2 and FLOW_TASK_IDS:
    _flow_settings_path = FLOW2["settings_path"]
    with open(_flow_settings_path, "rb") as _flow_settings_file:
        _flow_settings = tomllib.load(_flow_settings_file)
    FLOW2_KEY = _flow_settings["global"]["api_key"]
    FLOW2_BASE = FLOW2["base_url"].rstrip("/")
    FLOW2_MODELS = FLOW2_BASE + FLOW2["endpoints"]["models"]
    FLOW2_GENERATE = FLOW2_BASE + FLOW2["endpoints"]["generate"].format(model=FLOW2["model"])
    FLOW2_CAP = max(1, int(FLOW2.get("concurrency", 1)))
else:
    FLOW2_CAP = 0

PROVIDERS = ([("c2a", C2A_CAP)] if (C2A_CAP > 0 and C2A) else []) + \
            ([("poloai", POLO_CAP)] if POLO_CAP > 0 else []) + \
            ([("wuyin", WY_CAP)] if (WY_CAP > 0 and WY) else [])
FLOW_PROVIDERS = ([("flow2api_banana2", FLOW2_CAP)] if (FLOW_TASK_IDS and FLOW2) else [])
if FLOW_TASK_IDS and not FLOW2:
    print("flow2api_banana2 provider missing"); sys.exit(1)
if REGULAR_TASK_IDS and not PROVIDERS:
    print("no general providers enabled"); sys.exit(1)
ALL_PROVIDERS = PROVIDERS + FLOW_PROVIDERS
if not ALL_PROVIDERS:
    print("no providers enabled"); sys.exit(1)
sems = {name: threading.Semaphore(cap) for name, cap in ALL_PROVIDERS}
PNAMES = [n for n, _ in PROVIDERS]

# ---- imgbb upload cache (dedup, per-key lock so same ref uploads once) ----
_imgbb_cache = {}
_keylocks = {}
_klock = threading.Lock()
def _kl(key):
    with _klock:
        if key not in _keylocks: _keylocks[key] = threading.Lock()
        return _keylocks[key]
def upload_imgbb(path, work):
    key = os.path.abspath(path)
    with _kl(key):
        if key in _imgbb_cache: return _imgbb_cache[key]
        ext = os.path.splitext(path)[1] or ".png"
        ascii_tmp = os.path.join(work, "up_" + uuid.uuid4().hex[:8] + ext)
        shutil.copy(path, ascii_tmp)
        last = ""
        for _ in range(4):
            r = subprocess.run(["curl.exe", "-s", "--max-time", "120", "-F",
                                "image=@" + ascii_tmp, IMGBB_URL], capture_output=True)
            try:
                resp = json.loads(r.stdout.decode("utf-8", "ignore"))
                if resp.get("success"):
                    url = resp["data"]["url"]; _imgbb_cache[key] = url; return url
                last = r.stdout.decode("utf-8", "ignore")[:80]
            except Exception:
                last = "imgbb parse/throttle"
            time.sleep(3)
        raise RuntimeError("imgbb fail: " + last)

def px_to_ratio(s):
    m = re.match(r"(\d+)x(\d+)$", str(s))
    if not m: return s
    w, h = int(m.group(1)), int(m.group(2)); r = w / h
    cands = {"1:1":1.0,"3:2":1.5,"2:3":.6667,"16:9":1.7778,"9:16":.5625,"4:3":1.3333,"3:4":.75}
    return min(cands, key=lambda k: abs(cands[k] - r))

def _resolve(r):
    return r if os.path.exists(r) else os.path.join(OUTDIR, r)

def refs_of(t):
    return t.get("refs") or []

def prompt_text(t):
    """Task carries either an inline 'prompt' (asset tasks) or a 'prompt_file' (grid tasks)."""
    if t.get("prompt"): return t["prompt"]
    pf = t.get("prompt_file")
    if not pf: raise RuntimeError("no prompt or prompt_file")
    if not os.path.exists(pf): pf = os.path.join(os.path.dirname(TASK_JSON), pf)
    return open(pf, encoding="utf-8").read()

def prompt_as_file(t, work):
    """curl -F prompt=<file keeps the UTF-8 prompt off the Windows command line."""
    p = os.path.join(work, "p.txt")
    open(p, "w", encoding="utf-8").write(prompt_text(t))
    return p

def ref_args(t, work, field):
    """Copy refs to ASCII temp names (curl -F chokes on Chinese paths); mount order preserved."""
    args = []
    for i, r in enumerate(refs_of(t)):
        rr = _resolve(r)
        if not os.path.exists(rr): raise RuntimeError("ref missing " + os.path.basename(r))
        rt = os.path.join(work, "r%d.png" % i); shutil.copy(rr, rt)
        args += ["-F", "%s=@%s;type=image/png" % (field, rt)]
    return args

def _openai_image_bytes(resp_path, work):
    """Shared OpenAI-compatible response parse: data[0].b64_json, else data[0].url."""
    if not os.path.exists(resp_path): raise RuntimeError("no resp")
    data = json.load(open(resp_path, encoding="utf-8"))
    if isinstance(data, dict) and data.get("error"): raise RuntimeError(str(data["error"])[:80])
    item = (data.get("data") or [{}])[0]
    if item.get("b64_json"): return base64.b64decode(item["b64_json"])
    if item.get("url"):
        o = os.path.join(work, "o.png")
        subprocess.run(["curl.exe","-s","-o",o,item["url"],"--max-time","300"], capture_output=True)
        return open(o, "rb").read()
    raise RuntimeError("no b64/url")

def _image_mime(path):
    head = open(path, "rb").read(16)
    if head.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if head.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP": return "image/webp"
    return mimetypes.guess_type(path)[0] or "image/png"

def _as_png(data, mime, work):
    """Keep the existing .png asset contract even when Flow2API returns JPEG."""
    if mime == "image/png" or data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data
    src = os.path.join(work, "flow_source.bin")
    dst = os.path.join(work, "flow_output.png")
    open(src, "wb").write(data)
    ps = (
        "Add-Type -AssemblyName System.Drawing;"
        "$img=[System.Drawing.Image]::FromFile('%s');"
        "try{$img.Save('%s',[System.Drawing.Imaging.ImageFormat]::Png)}finally{$img.Dispose()}"
    ) % (src.replace("'", "''"), dst.replace("'", "''"))
    result = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    if result.returncode != 0 or not os.path.exists(dst):
        raise RuntimeError("Flow2API image-to-PNG conversion failed")
    return open(dst, "rb").read()

def preflight_flow2api():
    req = urllib.request.Request(
        FLOW2_MODELS,
        headers={"Authorization": "Bearer " + FLOW2_KEY, "x-goog-api-key": FLOW2_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            model_data = json.load(resp)
    except Exception as exc:
        raise RuntimeError("Flow2API preflight failed: " + str(exc))
    wanted = "models/" + FLOW2["model"]
    names = {m.get("name") for m in model_data.get("models", [])}
    if wanted not in names:
        raise RuntimeError("Flow2API model unavailable: " + FLOW2["model"])

# ---- provider generators: return image bytes or raise ----
def gen_c2a(t, work):
    """chatgpt2api local deploy: sync OpenAI-compatible. refs -> edits(multipart), none -> generations(JSON).
    size takes OpenAI pixel form directly (no ratio conversion). Local files upload straight, no imgbb."""
    resp = os.path.join(work, "resp.json")
    if os.path.exists(resp): os.remove(resp)
    if refs_of(t):
        args = ["curl.exe","-s","-X","POST",C2A_EDIT,
                "-H","Authorization: Bearer " + C2A["api_key"],
                "-F","model=" + C2A["model"], "-F","prompt=<" + prompt_as_file(t, work),
                "-F","n=1", "-F","size=" + str(t["size"])]
        args += ref_args(t, work, "image[]")
    else:
        body = {"model": C2A["model"], "prompt": prompt_text(t), "n": 1, "size": str(t["size"])}
        reqf = os.path.join(work, "req.json")
        open(reqf, "w", encoding="utf-8").write(json.dumps(body, ensure_ascii=False))
        args = ["curl.exe","-s","-X","POST",C2A_GEN,
                "-H","Authorization: Bearer " + C2A["api_key"],
                "-H","Content-Type: application/json", "--data-binary","@" + reqf]
    subprocess.run(args + ["-o", resp, "--max-time", "600"], capture_output=True)
    return _openai_image_bytes(resp, work)

def gen_flow2api_banana2(t, work):
    refs = refs_of(t)
    if len(refs) != 1:
        raise RuntimeError("Flow2API colored-pencil task requires exactly one source ref")
    ref = _resolve(refs[0])
    if not os.path.exists(ref):
        raise RuntimeError("ref missing " + os.path.basename(refs[0]))
    payload = {
        "systemInstruction": {"parts": [{"text": "Return exactly one image and no prose."}]},
        "contents": [{
            "role": "user",
            "parts": [
                {"inlineData": {"mimeType": _image_mime(ref), "data": base64.b64encode(open(ref, "rb").read()).decode("ascii")}},
                {"text": FLOW2["prompt"]},
            ],
        }],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {
                "aspectRatio": FLOW2["aspect_ratio"],
                "imageSize": FLOW2["image_size"],
            },
        },
    }
    req = urllib.request.Request(
        FLOW2_GENERATE,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"x-goog-api-key": FLOW2_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=420) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError("Flow2API HTTP %d: %s" % (exc.code, detail))
    found = None
    for candidate in body.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                found = inline
                break
        if found: break
    if not found:
        raise RuntimeError("Flow2API returned no inline image")
    mime = found.get("mimeType") or found.get("mime_type") or "image/jpeg"
    return _as_png(base64.b64decode(found["data"]), mime, work)

def gen_poloai(t, work):
    args = ["curl.exe","-s","-X","POST",POLO_EP,
            "-H","Authorization: Bearer " + POLO["api_key"],
            "-F","model=" + POLO["model"], "-F","prompt=<" + prompt_as_file(t, work),
            "-F","size=" + str(t["size"])]
    args += ref_args(t, work, "image[]")
    resp = os.path.join(work, "resp.json")
    if os.path.exists(resp): os.remove(resp)
    subprocess.run(args + ["-o", resp, "--max-time", "600"], capture_output=True)
    return _openai_image_bytes(resp, work)

def gen_wuyin(t, work):
    prompt = prompt_text(t)
    ratio = px_to_ratio(t["size"])
    urls = []
    for r in refs_of(t):
        rr = _resolve(r)
        if not os.path.exists(rr): raise RuntimeError("ref missing " + os.path.basename(r))
        urls.append(upload_imgbb(rr, work))
    body = {"prompt": prompt, "size": ratio}
    if urls: body["urls"] = urls
    reqf = os.path.join(work, "req.json")
    open(reqf, "w", encoding="utf-8").write(json.dumps(body, ensure_ascii=False))
    subf = os.path.join(work, "sub.json")
    if os.path.exists(subf): os.remove(subf)
    subprocess.run(["curl.exe","-s","-o",subf,"-X","POST",WY_SUBMIT,
                    "-H","Authorization: " + WY["api_key"], "-H","Content-Type: application/json",
                    "--data","@" + reqf], capture_output=True)
    if not os.path.exists(subf): raise RuntimeError("no submit resp")
    sub = json.load(open(subf, encoding="utf-8"))
    tid = (sub.get("data") or {}).get("task_id") or (sub.get("data") or {}).get("id")
    if sub.get("code") != 200 or not tid: raise RuntimeError("submit rejected")
    imgurl = None; detf = os.path.join(work, "det.json")
    for _ in range(40):
        time.sleep(8)
        if os.path.exists(detf): os.remove(detf)
        subprocess.run(["curl.exe","-s","-o",detf, WY_DETAIL + "?id=" + str(tid),
                        "-H","Authorization: " + WY["api_key"]], capture_output=True)
        if not os.path.exists(detf): continue
        det = json.load(open(detf, encoding="utf-8"))
        st = (det.get("data") or {}).get("status")
        if st == 2: imgurl = det["data"]["result"][0]; break
        if st == 3: raise RuntimeError("task failed")
    if not imgurl: raise RuntimeError("poll timeout")
    o = os.path.join(work, "o.png")
    # scapi.net result: curl TLS fails -> download via powershell IWR
    subprocess.run(["powershell","-NoProfile","-Command",
                    "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;"
                    "Invoke-WebRequest -Uri '%s' -OutFile '%s' -UseBasicParsing -TimeoutSec 300" % (imgurl, o)],
                   capture_output=True)
    if not os.path.exists(o): raise RuntimeError("download failed")
    return open(o, "rb").read()

GENERATORS = {
    "c2a": gen_c2a,
    "flow2api_banana2": gen_flow2api_banana2,
    "poloai": gen_poloai,
    "wuyin": gen_wuyin,
}

# ---- topological layers from refs deps ----
# Resolve dependencies by task output filename so asset chains (realistic -> colored
# pencil) and grid chains use the same ordering mechanism. Refs not produced by the
# current plan are treated as already-generated external inputs.
file_to_tid = {os.path.normcase(os.path.basename(t["file"])): tid for tid, t in tasks.items()}
def deps_of(t):
    return [file_to_tid[k] for r in refs_of(t)
            for k in [os.path.normcase(os.path.basename(r))] if k in file_to_tid]
depth = {}
def get_depth(tid):
    if tid in depth: return depth[tid]
    # deps outside this task file = already-generated grids on disk -> not a dependency, depth 0
    ds = [x for x in deps_of(tasks[tid]) if x in tasks]
    depth[tid] = 0 if not ds else 1 + max(get_depth(x) for x in ds)
    return depth[tid]
for tid in tasks: get_depth(tid)
layers = {}
for tid, d in depth.items(): layers.setdefault(d, []).append(tid)

def run_task(tid, primary):
    t = tasks[tid]
    outFile = os.path.join(OUTDIR, t["file"])
    if os.path.exists(outFile): return (tid, "SKIP")
    if is_flow2_colored_task(t):
        order = ["flow2api_banana2"]
    else:
        order = [primary] + [p for p in PNAMES if p != primary]
    last = ""
    for prov in order:
        tries = 1 if prov == "flow2api_banana2" else (6 if prov == primary else 2)
        for attempt in range(tries):
            work = os.path.join(TMP, tid + "_" + uuid.uuid4().hex[:6])
            os.makedirs(work, exist_ok=True)
            try:
                with sems[prov]:
                    data = GENERATORS[prov](t, work)
                if data and len(data) >= 1024:
                    open(outFile, "wb").write(data)
                    return (tid, "OK[%s]%s" % (prov, "" if attempt == 0 and prov == primary else "*"))
                last = "tiny/no data"
            except Exception as e:
                last = "%s:%s" % (prov, str(e)[:70])
            finally:
                shutil.rmtree(work, ignore_errors=True)
            time.sleep(4)
    return (tid, "FAIL " + last)

if FLOW_TASK_IDS:
    preflight_flow2api()
print("providers: " + ", ".join("%s(cap%d)" % (n, c) for n, c in ALL_PROVIDERS), flush=True)
print("layers: " + ", ".join("L%d=%d" % (k, len(v)) for k, v in sorted(layers.items())), flush=True)
ok = fail = skip = 0
poolsize = sum(c for _, c in ALL_PROVIDERS)
for lyr in sorted(layers):
    ids = sorted(layers[lyr], key=lambda x: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", x)])
    print("=== layer %d (%d tasks) ===" % (lyr, len(ids)), flush=True)
    with ThreadPoolExecutor(max_workers=poolsize) as ex:
        futs = [ex.submit(
            run_task,
            tid,
            "flow2api_banana2" if is_flow2_colored_task(tasks[tid]) else PNAMES[i % len(PNAMES)],
        ) for i, tid in enumerate(ids)]
        for f in futs:
            tid, res = f.result()
            print("  %s: %s" % (tid, res), flush=True)
            if res.startswith("OK"): ok += 1
            elif res == "SKIP": skip += 1
            else: fail += 1
print("\nDone. ok=%d fail=%d skip=%d" % (ok, fail, skip), flush=True)
