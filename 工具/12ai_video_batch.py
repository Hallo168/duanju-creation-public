#!/usr/bin/env python3
"""Submit and collect 12AI asynchronous video jobs from the unified API config.

12AI requires public reference-image URLs.  The URL mapping is therefore an
explicit input; this tool validates it and never silently replaces the selected
model or reference order.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from canvas_video_batch import (
    load_video_provider_config,
    mount_tokens,
    parse_units,
    resolve_mount,
    unit_section,
    video_prompt,
)
from video_reference_utils import imgbb_key, imgbb_upload, load_cache
from video_provider_catalog import video_model_rows


DEFAULT_BASE_URL = "https://cdn.12ai.org"
PROVIDER_NAME = "12ai_video"
ENV_NAME = "TWELVEAI_API_KEY"


def config() -> dict:
    path = Path(__file__).with_name("api配置.json")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def endpoint(provider: dict, name: str, method: str = "GET", base_url: str | None = None) -> str:
    value = str((provider.get("endpoints") or {}).get(name) or "")
    if not value:
        raise RuntimeError(f"providers.{PROVIDER_NAME}.endpoints.{name} 缺失")
    verb, url = value.split(" ", 1)
    if verb != method:
        raise RuntimeError(f"{name} 端点不是 {method}")
    selected_base_url = base_url or str(provider.get("base_url") or "")
    return (url.replace("{base_url}", selected_base_url)
               .replace("{models_base_url}", str(provider.get("models_base_url") or provider.get("base_url") or "")))


def api_json(url: str, token: str, method: str = "GET", payload: dict | None = None, timeout: int = 180) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36",
    }
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"网络请求失败: {exc}") from exc


def model_names(payload: object) -> set[str]:
    names: set[str] = set()
    if isinstance(payload, dict):
        for key in ("id", "model", "name"):
            if isinstance(payload.get(key), str):
                names.add(payload[key])
        for value in payload.values():
            names.update(model_names(value))
    elif isinstance(payload, list):
        for value in payload:
            names.update(model_names(value))
    return names


def validate_public_refs(raw: object, unit: int, expected_count: int) -> list[dict]:
    if not isinstance(raw, list) or len(raw) != expected_count:
        raise ValueError(f"单元{unit}公开参考图数量必须与视频挂载清单一致（期望 {expected_count}）")
    refs: list[dict] = []
    for index, value in enumerate(raw, 1):
        ref = {"url": value} if isinstance(value, str) else value
        if not isinstance(ref, dict) or not isinstance(ref.get("url"), str):
            raise ValueError(f"单元{unit}第{index}个参考图必须是 https URL 或含 url 的对象")
        parsed = urllib.parse.urlparse(ref["url"])
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"单元{unit}第{index}个参考图不是公开 https URL")
        refs.append(ref)
    return refs


def duration(section: str, unit: int) -> int:
    match = re.search(r"(?m)^生成参数：\s*(\d+)秒", section)
    if not match:
        raise ValueError(f"单元{unit}未找到生成参数时长")
    return int(match.group(1))


def prepared_units(story_root: Path, prompt_file: str, units: list[int], refs_by_unit: dict, partial: bool) -> dict[int, dict]:
    markdown = (story_root / prompt_file).read_text(encoding="utf-8")
    if not re.search(r"(?m)^(?:fenjing_ready|grids_ready):\s*true\s*$", markdown) and not partial:
        raise RuntimeError("fenjing_ready 不是 true（老集为 grids_ready）；仅用户明确点名放行部分单元时可加 --allow-partial-grids")
    result = {}
    for unit in units:
        section = unit_section(markdown, unit)
        prompt = video_prompt(section, unit)
        mounts = mount_tokens(section, unit)
        raw_refs = refs_by_unit.get(str(unit), refs_by_unit.get(f"U{unit}"))
        refs = validate_public_refs(raw_refs, unit, len(mounts))
        result[unit] = {"prompt": prompt, "references": refs, "duration": duration(section, unit)}
        print(f"PREFLIGHT unit={unit} refs={len(refs)} duration={result[unit]['duration']}", flush=True)
    return result


def append_ledger(path: Path, entry: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def require_current_model(provider: dict, token: str, model: str) -> None:
    names = model_names(video_model_rows(api_json(endpoint(provider, "models"), token, timeout=30)))
    if model not in names:
        raise RuntimeError("当前 12AI 模型表不包含用户确认的完整模型名，停止操作")


def local_reference_paths(
    story_root: Path,
    prompt_file: str,
    units: list[int],
    partial: bool,
) -> dict[int, list[Path]]:
    markdown = (story_root / prompt_file).read_text(encoding="utf-8")
    if not re.search(r"(?m)^(?:fenjing_ready|grids_ready):\s*true\s*$", markdown) and not partial:
        raise RuntimeError("fenjing_ready 不是 true（老集为 grids_ready）；仅用户明确点名放行部分单元时可加 --allow-partial-grids")
    result: dict[int, list[Path]] = {}
    for unit in units:
        section = unit_section(markdown, unit)
        prompt = video_prompt(section, unit)
        mounts = mount_tokens(section, unit)
        paths = [resolve_mount(story_root, token, unit) for token in mounts]
        for path in paths:
            if not path.is_file() or path.stat().st_size == 0:
                raise FileNotFoundError(path)
        used_indexes = [int(value) for value in re.findall(r"图片(\d+)", prompt)]
        if used_indexes and max(used_indexes) > len(paths):
            raise ValueError(
                f"单元{unit}图片编号超过挂载数: max={max(used_indexes)}, mounts={len(paths)}"
            )
        result[unit] = paths
    return result


def auto_public_references(
    paths_by_unit: dict[int, list[Path]],
    cache_path: Path,
) -> dict[str, list[str]]:
    key = imgbb_key()
    cache = load_cache(cache_path)
    unique_paths = sorted({path for paths in paths_by_unit.values() for path in paths}, key=str)
    urls = {path: imgbb_upload(path, key, cache, cache_path) for path in unique_paths}
    return {str(unit): [urls[path] for path in paths] for unit, paths in paths_by_unit.items()}


def preflight(args: argparse.Namespace) -> int:
    provider = config()["providers"][PROVIDER_NAME]
    token, base_url = load_video_provider_config(PROVIDER_NAME, ENV_NAME, DEFAULT_BASE_URL)
    base_url = (args.base_url or base_url).rstrip("/")
    require_current_model(provider, token, args.model)
    story_root = Path(args.story_root).resolve()
    units = parse_units(args.units)
    if args.reference_urls:
        refs_by_unit = json.loads(Path(args.reference_urls).read_text(encoding="utf-8-sig"))
        prepared_units(story_root, args.prompt_file, units, refs_by_unit, args.allow_partial_grids)
    elif args.auto_public_refs:
        imgbb_key()
        paths_by_unit = local_reference_paths(
            story_root,
            args.prompt_file,
            units,
            args.allow_partial_grids,
        )
        markdown = (story_root / args.prompt_file).read_text(encoding="utf-8")
        for unit, paths in paths_by_unit.items():
            section = unit_section(markdown, unit)
            print(
                f"PREFLIGHT unit={unit} refs={len(paths)} duration={duration(section, unit)} "
                f"resolution={args.resolution} public_upload=pending",
                flush=True,
            )
    else:
        raise RuntimeError("12AI 预检需要 --reference-urls 或 --auto-public-refs")
    print("BUDGET unknown", flush=True)
    return 0


def submit(args: argparse.Namespace) -> int:
    provider = config()["providers"][PROVIDER_NAME]
    token, base_url = load_video_provider_config(PROVIDER_NAME, ENV_NAME, DEFAULT_BASE_URL)
    base_url = (args.base_url or base_url).rstrip("/")
    require_current_model(provider, token, args.model)
    story_root = Path(args.story_root).resolve()
    units = parse_units(args.units)
    task_dir = Path(args.task_dir).resolve()
    task_dir.mkdir(parents=True, exist_ok=True)
    if args.reference_urls:
        refs_by_unit = json.loads(Path(args.reference_urls).read_text(encoding="utf-8-sig"))
    elif args.auto_public_refs:
        paths_by_unit = local_reference_paths(
            story_root,
            args.prompt_file,
            units,
            args.allow_partial_grids,
        )
        refs_by_unit = auto_public_references(paths_by_unit, Path(args.image_cache).resolve())
        reference_file = task_dir / f"12ai-reference-urls-{args.version}.json"
        reference_file.write_text(
            json.dumps(refs_by_unit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"REFERENCE_MAP path={reference_file}", flush=True)
    else:
        raise RuntimeError("12AI 提交需要 --reference-urls 或 --auto-public-refs")
    prepared = prepared_units(story_root, args.prompt_file, units, refs_by_unit, args.allow_partial_grids)
    ledger = Path(args.ledger).resolve()
    if ledger.exists() and ledger.stat().st_size:
        raise FileExistsError(f"账本已存在且非空，拒绝重复提交: {ledger}")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    errors = {}
    for unit in units:
        item = prepared[unit]
        body = {"model": args.model, "input": {"prompt": item["prompt"], "image_references": item["references"], "aspect_ratio": "9:16", "resolution": args.resolution, "duration": item["duration"], "audio": not args.no_audio, "n": 1}}
        request_path = task_dir / f"12ai-unit{unit}-{args.version}-request.json"
        request_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            data = api_json(endpoint(provider, "submit", "POST", base_url), token, "POST", body, timeout=240)
            response_path = task_dir / f"12ai-unit{unit}-{args.version}-response.json"
            response_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            task_id = str(data.get("id") or data.get("task_id") or "")
            if not task_id:
                raise RuntimeError("提交响应缺少任务 id")
            append_ledger(ledger, {"platform": PROVIDER_NAME, "unit": unit, "model": args.model, "task_id": task_id, "resolution": args.resolution, "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "last_status": data.get("status") or data.get("phase") or "queued"})
            print(f"SUBMITTED unit={unit} task_id={task_id}", flush=True)
        except Exception as exc:
            errors[unit] = str(exc)
            print(f"SUBMIT_ERROR unit={unit} {exc}", file=sys.stderr)
    return 1 if errors else 0


def find_video_url(value: object) -> str | None:
    if isinstance(value, str) and re.search(r"\.(mp4|mov|webm)(?:[?#]|$)", value, re.I):
        return value
    if isinstance(value, dict):
        for item in value.values():
            found = find_video_url(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = find_video_url(item)
            if found:
                return found
    return None


def poll(args: argparse.Namespace) -> int:
    provider = config()["providers"][PROVIDER_NAME]
    token, base_url = load_video_provider_config(PROVIDER_NAME, ENV_NAME, DEFAULT_BASE_URL)
    base_url = (args.base_url or base_url).rstrip("/")
    ledger_path = Path(args.ledger).resolve()
    entries = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    pending = {int(row["unit"]): str(row["task_id"]) for row in entries if row.get("task_id")}
    output_dir = (Path(args.story_root).resolve() / args.prompt_file).parent / "成片"
    output_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + args.timeout
    errors = {}
    while pending and time.time() < deadline:
        for unit, task_id in list(pending.items()):
            data = api_json(endpoint(provider, "detail", "GET", base_url).replace("{task_id}", urllib.parse.quote(task_id)), token, timeout=30)
            state = str(data.get("phase") or data.get("status") or "").lower()
            print(f"STATUS unit={unit} status={state or 'unknown'}", flush=True)
            if state in {"completed", "succeeded", "success"}:
                url = find_video_url(data)
                if not url:
                    errors[unit] = "成功状态缺少视频 URL"
                else:
                    target = output_dir / f"{unit}单元{args.output_suffix}.mp4"
                    temp = target.with_suffix(".mp4.part")
                    urllib.request.urlretrieve(url, temp)
                    if not temp.is_file() or temp.stat().st_size == 0:
                        errors[unit] = "下载结果为空"
                    else:
                        os.replace(temp, target)
                        append_ledger(ledger_path, {"platform": PROVIDER_NAME, "unit": unit, "task_id": task_id, "last_status": "succeeded", "output_path": str(target), "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
                pending.pop(unit)
            elif state in {"failed", "error", "canceled", "cancelled"}:
                errors[unit] = str(data.get("error") or data.get("message") or state)
                append_ledger(ledger_path, {"platform": PROVIDER_NAME, "unit": unit, "task_id": task_id, "last_status": state, "fail_reason": errors[unit], "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
                pending.pop(unit)
        if pending:
            time.sleep(args.interval)
    for unit in pending:
        errors[unit] = "轮询超时"
    for unit, error in sorted(errors.items()):
        print(f"FINAL_ERROR unit={unit} {error}", file=sys.stderr)
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["preflight", "submit", "poll"])
    parser.add_argument("--story-root", required=True)
    parser.add_argument("--ledger", default="")
    parser.add_argument("--units", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--reference-urls", help="每单元公开参考图 URL 的 JSON 映射；12AI 必填")
    parser.add_argument(
        "--auto-public-refs",
        action="store_true",
        help="按挂载顺序把本地参考图上传到项目配置的 imgbb；仅在用户明确确认后用于 submit",
    )
    parser.add_argument("--image-cache", default="", help="自动公开参考图的 sha256 URL 缓存")
    parser.add_argument("--task-dir", help="请求/响应临时文件目录；12AI submit 必填")
    parser.add_argument("--prompt-file", default="分镜-30秒/分镜提示词.md")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--resolution", default="480p")
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--allow-partial-grids", action="store_true")
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--version", default="v1")
    parser.add_argument("--output-suffix", default="-12ai")
    args = parser.parse_args()
    if args.mode in {"preflight", "submit"} and (not args.units or not args.model):
        parser.error(f"{args.mode} 需要 --units --model")
    if args.mode in {"preflight", "submit"} and not (args.reference_urls or args.auto_public_refs):
        parser.error(f"{args.mode} 需要 --reference-urls 或 --auto-public-refs")
    if args.mode == "submit" and not args.task_dir:
        parser.error("submit 需要 --task-dir")
    if args.mode == "submit" and args.auto_public_refs and not args.image_cache:
        parser.error("--auto-public-refs 提交需要 --image-cache")
    if args.mode in {"submit", "poll"} and not args.ledger:
        parser.error(f"{args.mode} 需要 --ledger")
    if args.mode == "preflight":
        return preflight(args)
    return submit(args) if args.mode == "submit" else poll(args)


if __name__ == "__main__":
    raise SystemExit(main())
