#!/usr/bin/env python3
"""YFAI video adapter. No automatic creation retries and no credential copies.

preflight only reads local files, estimates prices and calls the free dry-run.
submit consumes an unchanged, unexpired plan; uploads then revalidates the quote.
poll only follows persisted task IDs, downloads and technically verifies videos.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib import error, parse, request
import uuid

from PIL import Image
from canvas_video_batch import mount_tokens, parse_units, resolve_mount, unit_section, video_prompt
from video_provider_catalog import model_rows, read_config, token_for
from mjnewapi_video_batch import probe, tool

ROOT = Path(__file__).resolve().parent.parent
PROVIDER = "yfai_video"
MAX_FILE_BYTES = 50 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def append(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"at": now(), "platform": PROVIDER, **row}, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def ledger_rows(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


@contextmanager
def exclusive(path):
    """A crashed process leaves a visible lock; reconciliation is explicit."""
    path = Path(str(path) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "at": now()}))
    except FileExistsError:
        raise RuntimeError("本批已有执行锁；请先核对运行进程和账本，不能重复提交或轮询") from None
    try:
        yield
    finally:
        path.unlink()


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ApiError(RuntimeError):
    def __init__(self, status, message, code=None):
        self.status = status
        self.code = code
        super().__init__(f"YFAI HTTP {status}: {message}")


class Client:
    def __init__(self, provider=None):
        self.provider = provider if provider is not None else read_config()["providers"][PROVIDER]
        if self.provider.get("disabled") or self.provider.get("enabled") is False:
            raise RuntimeError("YFAI 渠道已停用")
        self.base = self.provider["base_url"].rstrip("/")
        if parse.urlsplit(self.base).scheme != "https":
            raise RuntimeError("YFAI 地址必须使用 HTTPS")
        self.token = token_for(self.provider)
        if not self.token:
            raise RuntimeError("YFAI 密钥不可用")
        self.opener = request.build_opener(NoRedirect)

    def clean(self, value):
        value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return value.replace(self.token, "[REDACTED]")

    def call(self, name, payload=None, blob=None, content_type=None, task_id="", timeout=30):
        method, endpoint = self.provider["endpoints"][name].split(" ", 1)
        endpoint = endpoint.replace("{task_id}", parse.quote(str(task_id), safe=""))
        if not endpoint.startswith("/"):
            raise RuntimeError("YFAI 端点必须位于配置的站点内")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json", "User-Agent": "ShortDramaYFAI/1.0"}
        if payload is not None:
            blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        if content_type:
            headers["Content-Type"] = content_type
        try:
            response = self.opener.open(request.Request(self.base + endpoint, data=blob, headers=headers, method=method), timeout=timeout)
        except error.HTTPError as exc:
            response = exc
        except (OSError, error.URLError) as exc:
            raise RuntimeError("YFAI 网络请求失败: " + self.clean(str(exc))) from None
        with response:
            status = response.code
            raw = self.clean(response.read(16_000_000).decode("utf-8", "replace"))
        try:
            data = json.loads(raw)
        except ValueError:
            raise ApiError(status, "响应不是 JSON，未重试") from None
        if not 200 <= status < 300:
            detail = data.get("error", {}) if isinstance(data, dict) else {}
            raise ApiError(status, self.clean(data)[:1200], detail.get("code") if isinstance(detail, dict) else None)
        task_failure = (name == "detail" and isinstance(data, dict)
                        and data.get("status") == "failed" and data.get("is_final") is True
                        and str(data.get("id") or data.get("task_id") or "") == str(task_id))
        if not isinstance(data, dict) or (data.get("error") and not task_failure):
            raise ApiError(status, self.clean(data)[:1200])
        return data


def catalog(client):
    return [row for row in model_rows(client.call("models")) if isinstance(row, dict)
            and row.get("category", row.get("type")) == "video"
            and not row.get("deprecated") and row.get("available") is not False]


def schema_value(schema, name, value):
    spec = schema.get(name)
    if not isinstance(spec, dict):
        raise RuntimeError(f"实时参数表未声明 {name}，不能静默使用默认值")
    values = spec.get("enum")
    if values is None and spec.get("options"):
        values = [row["value"] for row in spec["options"] if "value" in row]
    if values is not None:
        for candidate in values:
            if str(candidate) == str(value):
                return candidate
        raise RuntimeError(f"模型不支持 {name}={value}；合法值：{values}")
    if spec.get("type") in ("int", "number", "integer"):
        number = Decimal(str(value))
        if not number.is_finite() or number < Decimal(str(spec.get("min", spec.get("minimum", "-Infinity")))) or number > Decimal(str(spec.get("max", spec.get("maximum", "Infinity")))):
            raise RuntimeError(f"{name} 超出实时参数范围")
        if spec.get("type") in ("int", "integer") and number != number.to_integral_value():
            raise RuntimeError(f"{name} 必须为整数")
        return int(number) if spec.get("type") in ("int", "integer") else float(number)
    if spec.get("type") in ("bool", "boolean") and not isinstance(value, bool):
        raise RuntimeError(f"{name} 必须为布尔值")
    return value


def build_params(model, item, urls):
    schema = model.get("param_schema") or {}
    mode = "reference" if urls else "text-to-video"
    params = {"prompt": item["prompt"], "mode": schema_value(schema, "mode", mode)}
    for key in ("duration", "resolution", "aspect_ratio"):
        params[key] = schema_value(schema, key, item[key])
    if model.get("max_prompt_chars") and len(item["prompt"]) > model["max_prompt_chars"]:
        raise RuntimeError("提示词超过模型长度上限，不能截断台词")
    image_spec = schema.get("images") or {}
    limit = image_spec.get("max_items")
    if urls and (limit is None or len(urls) > int(limit)):
        raise RuntimeError("参考图数量超过模型上限或模型未声明上限")
    if urls:
        params["images"] = urls
    for ref in item.get("references", []):
        if ref["bytes"] > int(image_spec.get("max_bytes") or MAX_FILE_BYTES):
            raise RuntimeError("参考图超过该模型大小上限")
        if image_spec.get("max_edge_px") and max(ref["dimensions"]) > image_spec["max_edge_px"]:
            raise RuntimeError("参考图超过该模型像素上限")
    # Freeze scalar defaults (including exact Chinese enum values) in the plan.
    for name, spec in schema.items():
        if name in params or spec.get("type") == "upload":
            continue
        if name == "count":
            params[name] = schema_value(schema, name, 1)
        elif "default" in spec:
            params[name] = schema_value(schema, name, spec["default"])
        elif spec.get("required"):
            raise RuntimeError(f"模型还需要参数 {name}，本次不能省略")
    for name, spec in schema.items():
        conditions = spec.get("available_when") or {}
        if name in params and any(params.get(key) not in values for key, values in conditions.items()):
            raise RuntimeError(f"当前模式下 {name} 不生效，已停止")
        if spec.get("type") == "upload" and spec.get("required") and not params.get(name):
            raise RuntimeError(f"模型需要 {name} 素材，不能只发图片")
    return params


def local_inputs(args):
    story = Path(args.story_root).resolve()
    prompt_path = (story / args.prompt_file).resolve()
    if not prompt_path.is_relative_to(story):
        raise RuntimeError("提示词路径超出当前故事目录")
    markdown = prompt_path.read_text(encoding="utf-8-sig")
    if not re.search(r"(?m)^fenjing_ready:\s*true\s*$", markdown):
        raise RuntimeError("fenjing_ready 必须为 true")
    units = parse_units(args.units)
    if not units:
        raise RuntimeError("请指定本批单元")
    items = []
    for unit in units:
        section = unit_section(markdown, unit)
        prompt = video_prompt(section, unit)
        if not prompt:
            raise RuntimeError("视频提示词不能为空")
        refs = []
        for token in mount_tokens(section, unit):
            exact = re.search(r"[（(]([^（）()]+\.(?:png|jpe?g|webp))[）)]", token, re.I)
            ref = ((story / "资产图" / exact[1]) if exact else resolve_mount(story, token, unit)).resolve()
            allowed = [(story / "资产图").resolve(), (ROOT / "产品图").resolve()]
            if not any(ref.is_relative_to(folder) for folder in allowed) or "历史版本" in ref.parts:
                raise RuntimeError("参考图不在当前资产或产品图库范围内")
            if ref.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or not ref.is_file() or not 0 < ref.stat().st_size <= MAX_FILE_BYTES:
                raise RuntimeError("参考图不存在、格式不支持或超过 50MB: " + ref.name)
            with Image.open(ref) as img:
                dimensions = list(img.size)
                img.verify()
            refs.append({"path": str(ref), "sha256": digest(ref.read_bytes()), "bytes": ref.stat().st_size, "dimensions": dimensions})
        indexes = set(map(int, re.findall(r"(?:图片|图)(\d+)", prompt)))
        if indexes != set(range(1, len(refs) + 1)):
            raise RuntimeError(f"单元{unit}的图片编号与挂载顺序不一致")
        duration = re.search(r"(?m)^生成参数：\s*(\d+)秒", section)
        ratio = re.search(r"(?m)^生成参数：[^\n]*?(\d+:\d+)", section)
        if not duration or int(duration[1]) <= 0 or not ratio:
            raise RuntimeError("分镜须明确标注有效时长及画幅；模型能力另按实时参数表核验")
        items.append({"unit": unit, "prompt": prompt, "duration": int(duration[1]), "aspect_ratio": ratio[1], "resolution": args.resolution, "references": refs})
    return {"story_root": str(story), "prompt_file": args.prompt_file, "model": args.model, "items": items}


def money(value):
    try:
        amount = Decimal(str(value).removeprefix("$"))
    except Exception:
        raise RuntimeError("平台没有返回可核实的费用") from None
    if not amount.is_finite() or amount < 0:
        raise RuntimeError("平台费用数值无效")
    return amount


def model_route(client, model):
    return dict((getattr(client, "provider", {}).get("model_routes") or {}).get(model) or {})


def quote(client, model, params):
    route = model_route(client, model)
    payload = {"model": model, "modality": "video", "params": params, "include_all_channels": True}
    if route.get("lane_id"):
        payload["pinned_lane_id"] = route["lane_id"]
    data = client.call("estimate", payload)
    if route.get("lane_id"):
        channels = data.get("channels") or []
        if (data.get("vendor") != route["vendor"] or not channels
                or any(row.get("lane") != route["lane_id"] for row in channels)):
            raise RuntimeError("平台估价未锁定指定渠道，已停止")
    values = {name: float(money(data.get(name))) for name in ("effective_total_rmb", "max_effective_total_rmb", "required_freeze_rmb")}
    if values["max_effective_total_rmb"] < values["effective_total_rmb"]:
        raise RuntimeError("平台费用上下限不一致")
    return {**values, "price_basis": data.get("price_basis"), "strategy": data.get("strategy"), "vendor": data.get("vendor"), "lane_id": route.get("lane_id")}


def walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def leaves(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from leaves(child)
    elif isinstance(value, list):
        for child in value:
            yield from leaves(child)
    else:
        yield value


def dry_run(client, model, params):
    route = model_route(client, model)
    payload = {"model_id": model, "modality": "video", "params": params}
    if route.get("lane_id"):
        payload["lane_id"] = route["lane_id"]
    try:
        data = client.call("dry_run", payload)
    except ApiError as exc:
        if exc.status == 501 and exc.code == "dry_run_transport_not_instrumented":
            return {"verified": False, "warning": "该上游暂不支持免费转发验证；已核对公开参数和费用，实际素材接收及输出参数待正式生成后核验。"}
        raise
    if data.get("would_call_vendor") is not False:
        raise RuntimeError("零费用验证响应不符合预期")
    if route.get("lane_id") and (data.get("pinned_lane") != route["lane_id"]
                                or (data.get("wire") or {}).get("vendor") != route["vendor"]):
        raise RuntimeError("平台转发未锁定指定渠道，已停止")
    if params.get("images") and not (data.get("reference_check") or {}).get("present_in_vendor_body"):
        raise RuntimeError("平台转发请求丢失参考图，已停止")
    body = (data.get("wire") or {}).get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            raise RuntimeError("无法核对平台实际转发的参数") from None
    pairs = list(walk(body))
    urls = params.get("images", [])
    if urls:
        image_fields = ("images", "imageUrls", "image_urls", "referenceImages", "reference_image_urls")
        groups = [list(leaves(value)) for key, value in pairs if key in image_fields and isinstance(value, list)]
        if not groups:
            groups = [[value for value in leaves(body) if isinstance(value, str) and value in urls]]
        if any(group != urls for group in groups):
            raise RuntimeError("平台转发的参考图数量或顺序发生变化，已停止")
    if any(str(value) != "1" for key, value in pairs if key in ("n", "count") and not isinstance(value, (dict, list))):
        raise RuntimeError("平台转发的生成数量不是 1，已停止")
    duration_aliases = ("duration", "seconds", "duration_seconds", "video_duration")
    # This TYJP adapter sends duration=1 alongside the actual seconds in video_duration.
    if (model == "wan-3-0-special" and (data.get("wire") or {}).get("vendor") == "TYJP"
            and any(key == "video_duration" for key, _ in pairs)
            and all(str(value) == "1" for key, value in pairs if key == "duration")):
        duration_aliases = ("seconds", "duration_seconds", "video_duration")
    for name, aliases in {"duration": duration_aliases, "resolution": ("resolution",), "aspect_ratio": ("aspect_ratio", "ratio", "aspectRatio"), "prompt": ("prompt",)}.items():
        values = [v for k, v in pairs if k in aliases and not isinstance(v, (dict, list))]
        if not values or any(str(value) != str(params[name]) for value in values):
            raise RuntimeError(f"平台转发的 {name} 与所选值不一致或无法核实，已停止")
    return {"verified": True, "vendor": (data.get("wire") or {}).get("vendor"), "reference_check": data.get("reference_check"), "reference_prep_skipped": data.get("reference_prep_skipped")}


def make_plan(client, args):
    inputs = local_inputs(args)
    model = next((row for row in catalog(client) if row.get("id", row.get("name")) == args.model), None)
    if model is None:
        raise RuntimeError("实时目录没有可用的完整视频模型名")
    checks = []
    quote_cache = {}
    for item in inputs["items"]:
        # Free dry-run never fetches these placeholders; original assets stay local.
        urls = [f"https://cdn.example.com/yfai-preflight/reference-{i}.png" for i in range(1, len(item["references"]) + 1)]
        params = build_params(model, item, urls)
        key = digest(params)
        if key not in quote_cache:
            quote_cache[key] = quote(client, args.model, params)
        checked = dry_run(client, args.model, params)
        checks.append({"unit": item["unit"], "params": params, "quote": quote_cache[key], "dry_run": checked})
    totals = {name: float(sum(money(row["quote"][name]) for row in checks)) for name in ("effective_total_rmb", "max_effective_total_rmb", "required_freeze_rmb")}
    balance = client.call("balance")
    available = float(money((balance.get("credits") or {}).get("available")))
    plan = {"provider": PROVIDER, "base_url": client.base, "route": model_route(client, args.model), "created_at": now(), "expires_at": time.time() + 600,
            "inputs": inputs, "input_hash": digest(inputs), "checks": checks, "totals": totals, "available_credits": available}
    if args.plan:
        save(args.plan, plan)
    warnings = list(dict.fromkeys(row["dry_run"]["warning"] for row in checks if row["dry_run"].get("warning")))
    print("YFAI_PREFLIGHT " + json.dumps({"totals": totals, "available_credits": available, "warnings": warnings, "units": [row["unit"] for row in checks], "parameters": [{k: v for k, v in row["params"].items() if k not in ("prompt", "images")} for row in checks]}, ensure_ascii=False), flush=True)
    if available + 0.000001 < totals["required_freeze_rmb"]:
        raise RuntimeError(f"可用 {available:.4f} 算力，本批预冻结上限 {totals['required_freeze_rmb']:.4f}，余额不足；未提交")
    return plan


def https_url(url):
    parsed = parse.urlsplit(str(url))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError("平台未返回有效的 HTTPS 素材或成片地址")
    return str(url)


def upload(client, ref, cache, cache_path):
    content = Path(ref["path"]).read_bytes()
    if digest(content) != ref["sha256"]:
        raise RuntimeError("参考图在预检后发生变化，请重新预检")
    cache_key = digest({"base": client.base, "sha256": ref["sha256"]})
    if cache_key in cache:
        return https_url(cache[cache_key]["url"])
    boundary = "yfai-" + uuid.uuid4().hex
    suffix = Path(ref["path"]).suffix.lower()
    mime = mimetypes.guess_type("image" + suffix)[0] or "application/octet-stream"
    blob = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="reference{suffix}"\r\nContent-Type: {mime}\r\n\r\n').encode("ascii") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
    data = client.call("files", blob=blob, content_type="multipart/form-data; boundary=" + boundary, timeout=120)
    url = https_url(data.get("url", ""))
    cache[cache_key] = {"url": url, "sha256": ref["sha256"], "uploaded_at": now()}
    save(cache_path, cache)
    return url


def submit(client, args):
    if not args.plan or not args.ledger or not args.task_dir:
        raise RuntimeError("提交需要预检计划、独立账本和任务目录")
    with exclusive(args.plan), exclusive(args.ledger):
        if ledger_rows(args.ledger):
            raise RuntimeError("账本已有创建记录；请按任务号对账，禁止重复提交")
        plan = read(args.plan)
        inputs = local_inputs(args)
        route = model_route(client, args.model)
        if plan.get("route", {}) != route:
            raise RuntimeError("指定渠道在预检后发生变化，请重新预检")
        if plan.get("provider") != PROVIDER or plan.get("base_url") != client.base or time.time() > plan["expires_at"] or plan.get("input_hash") != digest(inputs) or digest(plan["inputs"]) != digest(inputs):
            raise RuntimeError("预检已过期、提示词、模型或参考图已变化，请重新预检")
        if Path(str(args.plan) + ".used").exists():
            raise RuntimeError("该预检已用于提交；先查询原任务，不能重复扣费")
        model = next((row for row in catalog(client) if row.get("id", row.get("name")) == args.model), None)
        if model is None:
            raise RuntimeError("所选模型当前不可用")
        directory = Path(args.task_dir)
        cache_path = directory / "references.json"
        cache = read(cache_path) if cache_path.exists() else {}
        prepared = []
        for item, check in zip(inputs["items"], plan["checks"], strict=True):
            if item["unit"] != check["unit"]:
                raise RuntimeError("预检单元不一致")
            urls = [upload(client, ref, cache, cache_path) for ref in item["references"]]
            params = build_params(model, item, urls)
            if {k: v for k, v in params.items() if k != "images"} != {k: v for k, v in check["params"].items() if k != "images"}:
                raise RuntimeError("模型参数或档位默认值已变化，请重新预检")
            current = quote(client, args.model, params)
            if any(money(current[name]) > money(check["quote"][name]) + Decimal("0.000001") for name in ("max_effective_total_rmb", "required_freeze_rmb")):
                raise RuntimeError("实时费用超过本次确认上限，请重新预检")
            checked = dry_run(client, args.model, params)
            if check["dry_run"].get("verified") and not checked.get("verified"):
                raise RuntimeError("当前上游已无法完成先前通过的免费验证，请重新预检")
            prepared.append((item, params, current))
        available = money(client.call("balance")["credits"]["available"])
        if available < sum(money(row[2]["required_freeze_rmb"]) for row in prepared):
            raise RuntimeError("余额不足以覆盖本批预冻结，未创建视频任务")
        # This marker is durable before the first POST, including process crashes.
        save(str(args.plan) + ".used", {"ledger": str(Path(args.ledger).resolve()), "at": now()})
        for item, params, current in prepared:
            request_id = "yfai-" + uuid.uuid4().hex
            body = {"model": args.model, **params, "client_request_id": request_id}
            if route.get("lane_id"):
                body["lane_id"] = route["lane_id"]
            unit = item["unit"]
            save(directory / f"unit{unit}-request.json", body)
            base = {"unit": unit, "model": args.model, "client_request_id": request_id, "duration": item["duration"],
                    "story_root": inputs["story_root"], "prompt_file": args.prompt_file,
                    "resolution": item["resolution"], "aspect_ratio": item["aspect_ratio"], "output_suffix": args.output_suffix,
                    "prompt_sha256": digest(item["prompt"].encode("utf-8")), "reference_sha256s": [ref["sha256"] for ref in item["references"]], "quote": current}
            append(args.ledger, {**base, "status": "submitting"})
            try:
                data = client.call("submit", body, timeout=180)
                task_id = data.get("task_id") or data.get("id")
                if not task_id:
                    raise RuntimeError("创建响应缺少任务号")
            except Exception as exc:
                state = "submit_failed" if isinstance(exc, ApiError) and 400 <= exc.status < 500 else "unknown_outcome"
                append(args.ledger, {**base, "status": state, "error": client.clean(str(exc)).replace(params["prompt"], "[PROMPT]")})
                raise RuntimeError(f"创建已停止（{state}），禁止自动重投: " + client.clean(str(exc))) from None
            save(directory / f"unit{unit}-response.json", data)
            append(args.ledger, {**base, "task_id": str(task_id), "status": "submitted"})
            print(f"SUBMIT unit={unit} task_id={task_id}", flush=True)


def task_state(data):
    if data.get("is_final") is not True:
        return "processing"
    if data.get("status") == "completed":
        https_url(data.get("output_url", ""))
        return "completed"
    if data.get("status") == "failed":
        return "failed"
    raise RuntimeError("平台返回未知终态，请人工核对；不重新生成")


def download(url, target):
    # CDN requests carry no API credentials, including after redirects.
    url = https_url(url)
    opener = request.build_opener(NoRedirect)
    for _ in range(6):
        try:
            response = opener.open(request.Request(url, headers={"User-Agent": "ShortDramaYFAI/1.0"}), timeout=150)
        except error.HTTPError as exc:
            response = exc
        with response:
            if response.code in (301, 302, 303, 307, 308):
                url = https_url(parse.urljoin(url, response.headers.get("Location", "")))
                continue
            if response.code != 200:
                raise RuntimeError(f"成片下载 HTTP {response.code}")
            with target.open("wb") as handle:
                shutil.copyfileobj(response, handle, 1024 * 1024)
            if target.stat().st_size == 0:
                raise RuntimeError("下载到空文件")
            return
    raise RuntimeError("成片下载重定向过多")


def collect(args, row, data):
    output = (Path(args.story_root) / args.prompt_file).resolve().parent / "成片"
    output.mkdir(parents=True, exist_ok=True)
    suffix = row["output_suffix"]
    target = output / f"{row['unit']}单元{suffix}.mp4"
    original = output / f"{row['unit']}单元{suffix}-原片.mp4"
    if not target.exists() and not original.exists():
        part = target.with_suffix(".mp4.part")
        download(data["output_url"], part)
        info = probe(part)
        video = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
        audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
        os.replace(part, target if video.get("codec_name") == "h264" and audio.get("codec_name") == "aac" else original)
    if not target.exists():
        info = probe(original)
        if not any(s["codec_type"] == "audio" for s in info["streams"]):
            raise RuntimeError("平台原片没有音轨，保留原片待核对")
        result = subprocess.run([tool("ffmpeg"), "-nostdin", "-v", "error", "-n", "-i", str(original), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(target)], capture_output=True, timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError("成片转码失败，原片已保留")
    info = probe(target)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
    width, height = video.get("width", 0), video.get("height", 0)
    duration = float(info["format"].get("duration", 0))
    ratio_w, ratio_h = map(int, row["aspect_ratio"].split(":"))
    edge = re.fullmatch(r"(\d+)p", row["resolution"])
    if not edge or min(width, height) != int(edge[1]) or not height or abs(width / height - ratio_w / ratio_h) > 0.02 or abs(duration - row["duration"]) > 0.75 or video.get("codec_name") != "h264" or audio.get("codec_name") != "aac":
        raise RuntimeError("成片编码、尺寸、画幅或时长不符；文件已保留待核对")
    decoded = subprocess.run([tool("ffmpeg"), "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode", "-i", str(target), "-f", "null", "-"], capture_output=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if decoded.returncode:
        raise RuntimeError("成片完整解码失败")
    return {"status": "succeeded", "output_path": str(target), "width": width, "height": height, "duration": duration,
            "video_codec": video["codec_name"], "audio_codec": audio["codec_name"], "sha256": digest(target.read_bytes()), "full_decode": True}


def poll(client, args):
    deadline = time.monotonic() + args.timeout
    attempted_collection = set()
    with exclusive(args.ledger):
        while True:
            latest = {}
            for row in ledger_rows(args.ledger):
                if row.get("platform") != PROVIDER:
                    raise RuntimeError("账本包含其他渠道任务")
                latest[row["unit"]] = {**latest.get(row["unit"], {}), **row}
            if not latest:
                raise RuntimeError("账本为空；查询不会创建新任务")
            pending = False
            failed = False
            for unit, row in latest.items():
                if row.get("story_root") != str(Path(args.story_root).resolve()) or row.get("prompt_file") != args.prompt_file:
                    raise RuntimeError("账本所属故事或分镜路径与查询参数不一致")
                if row["status"] in ("succeeded", "failed", "submit_failed", "unknown_outcome", "submitting") or row["status"] == "verification_failed" and unit in attempted_collection:
                    failed |= row["status"] != "succeeded"
                    continue
                if not row.get("task_id"):
                    raise RuntimeError("账本缺少任务号")
                try:
                    data = client.call("detail", task_id=row["task_id"])
                    state = task_state(data)
                except Exception as exc:
                    append(args.ledger, {"unit": unit, "status": "query_error", "error": client.clean(str(exc))})
                    pending = True
                    continue
                record = {"unit": unit, "task_id": row["task_id"], "status": state,
                          "remote_status": data.get("status"), "is_final": data.get("is_final"), "progress": data.get("progress"),
                          "actual_cost_micro_usd": data.get("actual_cost_micro_usd")}
                if state == "failed":
                    record.update(error=client.clean(data.get("error", "生成失败")), error_class=data.get("error_class"))
                    failed = True
                elif state == "completed":
                    attempted_collection.add(unit)
                    try:
                        record.update(collect(args, row, data))
                    except Exception as exc:
                        record.update(status="verification_failed", error=client.clean(str(exc)))
                        failed = True
                else:
                    pending = True
                append(args.ledger, record)
                print("POLL " + json.dumps(record, ensure_ascii=False), flush=True)
            if not pending:
                return 1 if failed else 0
            if time.monotonic() >= deadline:
                print("仍有任务处理中；可用同一账本继续 poll，不要重新提交。", flush=True)
                return 2
            time.sleep(args.interval)


def main():
    provider = read_config()["providers"][PROVIDER]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "preflight", "submit", "poll", "status"])
    parser.add_argument("--story-root")
    parser.add_argument("--prompt-file", default="分镜-30秒/分镜提示词.md")
    parser.add_argument("--units", default="")
    parser.add_argument("--model", default=provider.get("default_model", ""))
    parser.add_argument("--resolution", default=provider.get("defaults", {}).get("resolution", "720p"))
    parser.add_argument("--plan")
    parser.add_argument("--ledger")
    parser.add_argument("--task-dir")
    parser.add_argument("--task-id")
    parser.add_argument("--output-suffix", default="-yfai-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--timeout", type=float, default=7200)
    args = parser.parse_args()
    if args.interval < 5 or args.timeout <= 0 or not re.fullmatch(r"[-A-Za-z0-9_]+", args.output_suffix):
        parser.error("轮询间隔至少 5 秒，超时为正数，版本后缀只能含字母、数字、横线或下划线")
    if args.action in ("preflight", "submit", "poll") and not args.story_root:
        parser.error("需要 --story-root")
    if args.action in ("preflight", "submit") and not args.model:
        parser.error("需要选择完整模型名")
    if args.action == "poll" and not args.ledger:
        parser.error("需要 --ledger")
    client = Client()
    if args.action == "check":
        models = catalog(client)
        balance = client.call("balance")
        print(json.dumps({"provider": PROVIDER, "connected": True, "available_video_models": len(models), "available_credits": balance.get("credits", {}).get("available")}, ensure_ascii=False))
    elif args.action == "status":
        if not args.task_id:
            parser.error("需要 --task-id")
        data = client.call("detail", task_id=args.task_id)
        print(json.dumps({key: data.get(key) for key in ("id", "status", "is_final", "progress", "error", "actual_cost_micro_usd")}, ensure_ascii=False))
    elif args.action == "preflight":
        make_plan(client, args)
    elif args.action == "submit":
        submit(client, args)
    else:
        return poll(client, args)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("ERROR " + str(exc), file=sys.stderr)
        raise SystemExit(1)
