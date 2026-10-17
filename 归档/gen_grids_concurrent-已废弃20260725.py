# -*- coding: utf-8 -*-
# Concurrent grid-image generation via poloai edits, topological layers.
# Chains run in parallel across layers; within a layer all tasks are independent.
# Usage: python gen_grids_concurrent.py [task_json] [max_workers]
import json, os, re, sys, shutil, base64, uuid, subprocess, time
from concurrent.futures import ThreadPoolExecutor

TASK_JSON = sys.argv[1] if len(sys.argv) > 1 else r".\工具\宫格任务-07-polo.json"
MAXW = int(sys.argv[2]) if len(sys.argv) > 2 else 6

CFG = json.load(open(r".\工具\api-config.json", encoding="utf-8"))
PLAN = json.load(open(TASK_JSON, encoding="utf-8"))
OUTDIR = PLAN["output_dir"]
ENDPOINT = CFG["base_url"].rstrip("/") + "/v1/images/edits"
TMP = os.path.join(os.environ["TEMP"], "gridcc")
os.makedirs(TMP, exist_ok=True)
os.makedirs(OUTDIR, exist_ok=True)

tasks = {t["id"]: t for t in PLAN["tasks"]}

def deps_of(t):
    d = []
    for r in t["refs"]:
        m = re.match(r"单元(\d+)-宫格\.png", os.path.basename(r))
        if m:
            d.append("U" + m.group(1))
    return d

depth = {}
def get_depth(tid):
    if tid in depth:
        return depth[tid]
    ds = deps_of(tasks[tid])
    depth[tid] = 0 if not ds else 1 + max(get_depth(x) for x in ds)
    return depth[tid]
for tid in tasks:
    get_depth(tid)
layers = {}
for tid, dep in depth.items():
    layers.setdefault(dep, []).append(tid)

def run_task(tid):
    t = tasks[tid]
    outFile = os.path.join(OUTDIR, t["file"])
    if os.path.exists(outFile):
        return (tid, "SKIP")
    work = os.path.join(TMP, tid + "_" + uuid.uuid4().hex[:6])
    os.makedirs(work, exist_ok=True)
    try:
        pf = os.path.join(work, "p.txt")
        shutil.copy(t["prompt_file"], pf)
        base = ["curl.exe", "-s", "-X", "POST", ENDPOINT,
                "-H", "Authorization: Bearer " + CFG["api_key"],
                "-F", "model=" + CFG["model"],
                "-F", "prompt=<" + pf,
                "-F", "size=" + t["size"]]
        for i, r in enumerate(t["refs"]):
            rr = r if os.path.exists(r) else os.path.join(OUTDIR, r)
            if not os.path.exists(rr):
                return (tid, "FAIL:ref missing " + os.path.basename(r))
            rt = os.path.join(work, "r%d.png" % i)
            shutil.copy(rr, rt)
            base += ["-F", "image[]=@%s;type=image/png" % rt]
        resp = os.path.join(work, "resp.json")
        args = base + ["-o", resp, "--max-time", "600"]
        last = ""
        for attempt in range(6):
            if attempt > 0:
                time.sleep(4)
            if os.path.exists(resp):
                os.remove(resp)
            subprocess.run(args, capture_output=True)
            if not os.path.exists(resp):
                last = "no resp"; continue
            try:
                data = json.load(open(resp, encoding="utf-8"))
            except Exception:
                last = "bad json"; continue
            if isinstance(data, dict) and data.get("error"):
                last = str(data["error"])[:90]; continue
            item = (data.get("data") or [{}])[0]
            outTmp = os.path.join(work, "out.png")
            if item.get("b64_json"):
                open(outTmp, "wb").write(base64.b64decode(item["b64_json"]))
            elif item.get("url"):
                subprocess.run(["curl.exe", "-s", "-o", outTmp, item["url"], "--max-time", "300"], capture_output=True)
            else:
                last = "no b64/url"; continue
            if not os.path.exists(outTmp) or os.path.getsize(outTmp) < 1024:
                last = "tiny/no file"; continue
            shutil.move(outTmp, outFile)
            return (tid, "OK" + ("" if attempt == 0 else " (retry %d)" % attempt))
        return (tid, "FAIL:" + last)
    finally:
        shutil.rmtree(work, ignore_errors=True)

print("layers: " + ", ".join("L%d=%d" % (k, len(v)) for k, v in sorted(layers.items())), flush=True)
ok = fail = skip = 0
for lyr in sorted(layers):
    ids = sorted(layers[lyr], key=lambda x: int(x[1:]))
    print("=== layer %d (%d tasks) ===" % (lyr, len(ids)), flush=True)
    with ThreadPoolExecutor(max_workers=MAXW) as ex:
        for tid, res in ex.map(run_task, ids):
            print("  %s: %s" % (tid, res), flush=True)
            if res.startswith("OK"): ok += 1
            elif res == "SKIP": skip += 1
            else: fail += 1
print("\nDone. ok=%d fail=%d skip=%d  (maxw=%d)" % (ok, fail, skip, MAXW), flush=True)
