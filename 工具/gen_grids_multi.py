# -*- coding: utf-8 -*-
# Concurrent image generation with topological layers and fixed task routing.
# Ordinary assets use chatgpt2api; colored-pencil derivatives use Flow2API Banana 2
# only when the production switch permits them. Failed tasks never switch routes.
# Usage: python gen_grids_multi.py <task_json> [c2a_cap]
# c2a_cap defaults to 6; use 0 for colored-pencil-only batches.
# Preflight ordinary batches against chatgpt2api /v1/models before submission.
# Enabled colored-pencil batches preflight Flow2API /models automatically.
import argparse
import json, os, re, sys, shutil, base64, uuid, subprocess, time, threading, mimetypes, tomllib
import urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

parser = argparse.ArgumentParser(description="Generate ordinary assets and colored-pencil references on their fixed routes.")
parser.add_argument("task_json")
parser.add_argument("c2a_cap", type=int, nargs="?", default=6)
args = parser.parse_args()
if args.c2a_cap < 0:
    parser.error("c2a_cap must be zero or greater")
TASK_JSON = args.task_json
C2A_CAP = args.c2a_cap

TOOLDIR = os.path.dirname(os.path.abspath(__file__))

def colored_pencil_enabled():
    policy_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "生产开关.json")
    try:
        with open(policy_file, encoding="utf-8") as policy_stream:
            enabled = json.load(policy_stream).get("colored_pencil_enabled")
    except FileNotFoundError:
        return True
    if not isinstance(enabled, bool):
        raise RuntimeError("invalid colored_pencil_enabled production setting")
    return enabled

def is_flow2_colored_task(t):
    """Colored-pencil derivatives have a dedicated route controlled by the production switch."""
    return t.get("provider") == "flow2api_banana2" or bool(
        re.search("彩铅|素描", os.path.basename(t.get("file", "")))
    )

PLAN = json.load(open(TASK_JSON, encoding="utf-8"))
OUTDIR = PLAN["output_dir"]
tasks = {t["id"]: t for t in PLAN["tasks"]}
if not colored_pencil_enabled():
    paused = {tid: task for tid, task in tasks.items() if is_flow2_colored_task(task)}
    paused_files = {os.path.normcase(os.path.basename(task["file"])) for task in paused.values()}
    for tid in paused:
        print(tid + ": SKIP[colored-pencil temporarily disabled]", flush=True)
        del tasks[tid]
    for tid, task in tasks.items():
        if any(os.path.normcase(os.path.basename(ref)) in paused_files or re.search("彩铅|素描", os.path.basename(ref)) for ref in task.get("refs", [])):
            print(tid + ": blocked; replace paused pencil reference with the matching realistic image before submission")
            sys.exit(2)
    if not tasks:
        print("Done. no image requests; colored-pencil tasks temporarily disabled", flush=True)
        sys.exit(0)

with open(os.path.join(TOOLDIR, "api配置.json"), encoding="utf-8") as config_file:
    CONFIG = json.load(config_file)
C2A = CONFIG.get("providers", {}).get("chatgpt2api")
FLOW2 = CONFIG.get("providers", {}).get("flow2api_banana2")

# Explicit caps must never override a provider disabled by the user.
if C2A_CAP > 0 and C2A and C2A.get("disabled"):
    print("chatgpt2api disabled in api configuration; stopped without submission")
    sys.exit(2)

TMP    = os.path.join(os.environ["TEMP"], "gridmulti")
os.makedirs(TMP, exist_ok=True); os.makedirs(OUTDIR, exist_ok=True)

for _tid, _task in tasks.items():
    if _task.get("provider") not in (None, "", "c2a", "chatgpt2api", "flow2api_banana2"):
        print("unsupported image provider in task: " + _tid)
        sys.exit(2)
    if "彩铅参考图" in os.path.basename(_task.get("file", "")) and not _task.get("refs"):
        print("colored-pencil task requires exactly one source ref: " + _tid)
        sys.exit(1)

FLOW_TASK_IDS = [tid for tid, t in tasks.items() if is_flow2_colored_task(t)]
REGULAR_TASK_IDS = [tid for tid, t in tasks.items() if not is_flow2_colored_task(t)]

if C2A:
    C2A_BASE = C2A["base_url"].rstrip("/")
    C2A_GEN  = C2A_BASE + "/v1/images/generations"
    C2A_EDIT = C2A_BASE + "/v1/images/edits"

if FLOW2 and FLOW_TASK_IDS:
    if FLOW2.get("disabled"):
        print("flow2api_banana2 disabled in api configuration; stopped without submission")
        sys.exit(2)
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

PROVIDERS = [("c2a", C2A_CAP)] if (C2A_CAP > 0 and C2A) else []
FLOW_PROVIDERS = ([("flow2api_banana2", FLOW2_CAP)] if (FLOW_TASK_IDS and FLOW2) else [])
if FLOW_TASK_IDS and not FLOW2:
    print("flow2api_banana2 provider missing"); sys.exit(1)
if REGULAR_TASK_IDS and not PROVIDERS:
    print("no general providers enabled"); sys.exit(1)
ALL_PROVIDERS = PROVIDERS + FLOW_PROVIDERS
if not ALL_PROVIDERS:
    print("no providers enabled"); sys.exit(1)
sems = {name: threading.Semaphore(cap) for name, cap in ALL_PROVIDERS}

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
    if not colored_pencil_enabled():
        raise RuntimeError("colored-pencil generation temporarily disabled")
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

GENERATORS = {
    "c2a": gen_c2a,
    "flow2api_banana2": gen_flow2api_banana2,
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

def run_task(tid, provider):
    t = tasks[tid]
    outFile = os.path.join(OUTDIR, t["file"])
    if os.path.exists(outFile): return (tid, "SKIP")
    last = ""
    tries = 1 if provider == "flow2api_banana2" else 6
    for attempt in range(tries):
        work = os.path.join(TMP, tid + "_" + uuid.uuid4().hex[:6])
        os.makedirs(work, exist_ok=True)
        try:
            with sems[provider]:
                data = GENERATORS[provider](t, work)
            if data and len(data) >= 1024:
                open(outFile, "wb").write(data)
                return (tid, "OK[%s]%s" % (provider, "" if attempt == 0 else "*"))
            last = "tiny/no data"
        except Exception as e:
            last = "%s:%s" % (provider, str(e)[:70])
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
            "flow2api_banana2" if is_flow2_colored_task(tasks[tid]) else "c2a",
        ) for tid in ids]
        for f in futs:
            tid, res = f.result()
            print("  %s: %s" % (tid, res), flush=True)
            if res.startswith("OK"): ok += 1
            elif res == "SKIP": skip += 1
            else: fail += 1
print("\nDone. ok=%d fail=%d skip=%d" % (ok, fail, skip), flush=True)
