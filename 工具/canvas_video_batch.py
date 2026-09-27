#!/usr/bin/env python3
"""Submit and collect Canvas video tasks using the unified API config."""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


DEFAULT_BASE_URL = "https://api.canvas.12646464.xyz"
DEFAULT_MODEL = ""
CONFIG_PATH = Path(__file__).with_name("api配置.json")


def load_video_provider_config(
    provider_name: str, env_name: str, default_base_url: str
) -> tuple[str, str]:
    """Load a video provider from api配置.json; an environment variable may override its key."""
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        provider = config.get("providers", {}).get(provider_name, {})
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取统一 API 配置 {CONFIG_PATH}: {exc}") from exc
    token = os.environ.get(env_name) or str(provider.get("api_key") or "")
    base_url = str(provider.get("base_url") or default_base_url)
    if not token:
        raise RuntimeError(f"统一 API 配置缺少 providers.{provider_name}.api_key")
    return token, base_url


def parse_units(value: str) -> list[int | float]:
    result: set[int | float] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            result.update(range(start, end + 1))
        else:
            number = float(part) if "." in part else int(part)
            result.add(number)
    return sorted(result)


def unit_section(markdown: str, unit: int | float) -> str:
    match = re.search(
        rf"(?ms)^## 单元{re.escape(str(unit))}\uff08.*?(?=^## 单元\d+(?:\.\d+)?\uff08|\Z)", markdown
    )
    if not match:
        raise ValueError(f"未找到单元{unit}")
    section = match.group(0)
    frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", markdown, re.S)
    if frontmatter and re.search(r'''(?m)^mode:[ \t]*["']?30秒["']?[ \t]*(?:#.*)?\r?$''', frontmatter[1]):
        duration = re.search(r"(?m)^生成参数：\s*(\d+)秒", section)
        if not duration or int(duration[1]) != 30:
            raise ValueError(f"单元{unit}属于30秒分镜，生成参数必须固定30秒")
    return section


def video_prompt(section: str, unit: int) -> str:
    # 现行契约为「### 单元提示词」（2026-07-23 宫格法废止）；老集宫格法文件回退「### 宫格转视频提示词」
    match = re.search(
        r"(?ms)^### 单元提示词\s*\n(?P<fence>`{3}|~{3})[^\r\n]*\r?\n(.*?)\r?\n(?P=fence)", section
    ) or re.search(
        r"(?ms)^### 宫格转视频提示词\s*\n(?P<fence>`{3}|~{3})[^\r\n]*\r?\n(.*?)\r?\n(?P=fence)", section
    )
    if not match:
        raise ValueError(f"单元{unit}未找到单元提示词（老集回退：宫格转视频提示词）")
    prompt = match.group(2).strip()
    # “退款”可能是合法电商对白，不能作为提示词元数据泄漏标记。
    forbidden = ("```", "修复记录：", "task_id", "任务号", "审核失败")
    leaked = [marker for marker in forbidden if marker in prompt]
    if leaked:
        raise ValueError(f"单元{unit}提示词混入代码块外元数据: {leaked}")
    return prompt


def mount_line(section: str, unit: int) -> str:
    match = re.search(r"(?m)^视频挂载\uff1a(.+)$", section)
    if not match:
        raise ValueError(f"单元{unit}未找到视频挂载")
    return match.group(1).strip()


def mount_tokens(section: str, unit: int) -> list[str]:
    """Parse both legacy slash-separated and current Chinese-semicolon mounts."""
    line = mount_line(section, unit).strip().rstrip("。.")
    tokens = [item.strip().rstrip("。.") for item in re.split(r"\s*(?:/|[；;])\s*", line)]
    tokens = [item for item in tokens if item]
    if not tokens:
        raise ValueError(f"单元{unit}视频挂载为空")
    if len(tokens) > 9:
        raise ValueError(f"单元{unit}参考图共{len(tokens)}张，项目上限为9张；请调整挂载或重排单元")
    return tokens


def clean_mount_token(token: str) -> str:
    token = re.sub(r"^第\d+个上传\s*[:：]?\s*", "", token.strip())
    return token.strip().strip("`").strip()


def resolve_grid_mount(root: Path, unit: int, grid_dir: Path | None) -> Path:
    if grid_dir is not None:
        candidates = sorted(grid_dir.glob(f"单元{unit}-宫格*.png"))
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ValueError(f"单元{unit}宫格匹配到多个文件: {[p.name for p in candidates]}")
        raise FileNotFoundError(grid_dir / f"单元{unit}-宫格*.png")
    matches = list((root / "宫格图").rglob(f"单元{unit}-宫格.png"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"单元{unit}宫格匹配到多个文件: {[p.name for p in matches]}")
    raise FileNotFoundError(root / "宫格图" / f"单元{unit}-宫格.png")


def resolve_mount(root: Path, token: str, unit: int | None = None, grid_dir: Path | None = None) -> Path:
    token = clean_mount_token(token)
    exact_asset = root / "资产图" / token
    if exact_asset.is_file() and "弃用" not in exact_asset.name:
        return exact_asset
    if "本单元宫格图" in token:
        if unit is None:
            raise ValueError("本单元宫格图需要当前单元号")
        return resolve_grid_mount(root, unit, grid_dir)
    grid = re.search(r"单元(\d+)宫格图", token)
    if grid:
        return resolve_grid_mount(root, int(grid.group(1)), grid_dir)

    asset_id = re.search(r"([RSP]\d+)", token)
    if asset_id:
        prefix = asset_id.group(1) + "-"
        candidates = [
            p
            for ext in ("png", "jpg", "jpeg", "webp")
            for p in (root / "资产图").rglob(f"{prefix}*.{ext}")
            if "弃用" not in p.name
        ]
        wanted = []
        if "彩铅" in token:
            wanted.append("彩铅参考图")
        if "素描头" in token:
            wanted.append("素描头参考图")
        if "3D参考图" in token:
            wanted.append("3D参考图")
        if "人物设定图" in token or "写实人物设定图" in token:
            wanted.append("人物设定图")
        if "四宫格" in token:
            wanted.append("四宫格")
        if wanted:
            candidates = [p for p in candidates if all(word in p.name for word in wanted)]
        # 产品状态别名：P1A/P1B/P1C/P1D 在分镜挂载清单中区分实拍状态，不能让
        # “防盗刷卡包”与文件名中的“防盗卡包”词差导致多张 P1 图同时命中。
        if "P1B" in token:
            candidates = [p for p in candidates if "五色闭合组合" in p.stem]
        elif "P1A" in token:
            candidates = [p for p in candidates if "镜面银闭合" in p.stem]
        elif "P1C" in token:
            candidates = [p for p in candidates if "打开风琴卡位" in p.stem]
        elif "P1D" in token:
            candidates = [p for p in candidates if "闭合与装载" in p.stem]
        product_descriptors = ("包装内容物", "佩戴效果")
        matched_descriptors = [word for word in product_descriptors if word in token]
        if matched_descriptors:
            candidates = [p for p in candidates if all(word in p.name for word in matched_descriptors)]
        if len(candidates) > 1:
            narrowed = []
            for path in candidates:
                parts = path.stem.split("-")[1:]
                identity_parts = [
                    part for part in parts
                    if part not in {"彩铅参考图", "人物设定图", "三视图", "四宫格", "全景"}
                ]
                if all(part in token for part in identity_parts):
                    narrowed.append(path)
            if narrowed:
                candidates = narrowed
        if len(candidates) > 1:
            stem_groups = {p.stem for p in candidates}
            png_candidates = [p for p in candidates if p.suffix.lower() == ".png"]
            if len(stem_groups) == 1 and len(png_candidates) == 1:
                return png_candidates[0]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ValueError(f"挂载项匹配到多个文件: {token} -> {[p.name for p in candidates]}")
    raise ValueError(f"无法解析挂载项: {token}")


def unit_inputs(
    story_root: Path,
    markdown: str,
    unit: int,
    grid_dir: Path | None = None,
) -> tuple[str, list[Path], int]:
    section = unit_section(markdown, unit)
    prompt = video_prompt(section, unit)
    tokens = mount_tokens(section, unit)
    paths = [resolve_mount(story_root, token, unit, grid_dir) for token in tokens]
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(path)
    used_indexes = [int(n) for n in re.findall(r"图片(\d+)", prompt)]
    if used_indexes and max(used_indexes) > len(paths):
        raise ValueError(
            f"单元{unit}图片编号超过挂载数: max={max(used_indexes)}, mounts={len(paths)}"
        )
    duration_match = re.search(r"(?m)^生成参数：\s*(\d+)秒", section)
    if not duration_match:
        raise ValueError(f"单元{unit}未找到生成时长")
    return prompt, paths, int(duration_match.group(1))


def unit_payload(
    story_root: Path,
    markdown: str,
    unit: int,
    model: str,
    resolution: str,
    grid_dir: Path | None = None,
) -> tuple[dict, list[Path]]:
    prompt, paths, duration = unit_inputs(story_root, markdown, unit, grid_dir)
    images = []
    for path in paths:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        images.append(f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii"))
    payload = {
        "model": model,
        "prompt": prompt,
        "images": images,
        "duration": duration,
        "aspect_ratio": "9:16",
        "ratio": "9:16",
        "resolution": resolution,
    }
    return payload, paths


def api_json(url: str, token: str, method: str = "GET", payload: dict | None = None, timeout: int = 180) -> dict:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    attempts = 5 if method == "GET" else 1
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if method == "GET" and exc.code in {429, 500, 502, 503, 504} and attempt < attempts:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.RemoteDisconnected) as exc:
            if method == "GET" and attempt < attempts:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"网络请求失败: {exc}") from exc
    raise RuntimeError("GET 重试耗尽")


def pick(obj: dict, *names: str):
    for name in names:
        if name in obj and obj[name] not in (None, ""):
            return obj[name]
    data = obj.get("data")
    if isinstance(data, dict):
        for name in names:
            if name in data and data[name] not in (None, ""):
                return data[name]
    return None


def append_ledger(path: Path, entry: dict, lock: threading.Lock) -> None:
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    with lock:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def require_current_video_model(base_url: str, token: str, model: str) -> dict:
    models = api_json(base_url + "/v1/models", token)
    rows = models.get("data") if isinstance(models, dict) else None
    selected = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("id") != model:
            continue
        endpoint_types = row.get("supported_endpoint_types") or []
        searchable = f"{row.get('id', '')} {row.get('name', '')}"
        is_video = (
            row.get("type") == "video"
            or "openai-video" in endpoint_types
            or "openai-videos" in endpoint_types
            or re.search(r"seedance|video|veo|kling|wan|hailuo|sora|luma|runway", searchable, re.I)
        )
        if is_video:
            selected.append(row)
    if len(selected) != 1:
        raise RuntimeError("实时模型表中不存在用户确认的完整视频模型名，停止操作")
    return selected[0]


def preflight(args: argparse.Namespace) -> int:
    token, configured_base_url = load_video_provider_config(
        "canvas_video", "CANVAS_VIDEO_API_TOKEN", DEFAULT_BASE_URL
    )
    base_url = (args.base_url or configured_base_url).rstrip("/")
    require_current_video_model(base_url, token, args.model)
    story_root = Path(args.story_root).resolve()
    markdown_path = story_root / args.prompt_file
    markdown = markdown_path.read_text(encoding="utf-8")
    ready = bool(re.search(r"(?m)^(?:fenjing_ready|grids_ready):\s*true\s*$", markdown))
    if not ready and not args.allow_partial_grids:
        raise RuntimeError("fenjing_ready 不是 true（老集为 grids_ready）；仅用户明确点名放行部分单元时可加 --allow-partial-grids")
    units = parse_units(args.units)
    grid_dir = (story_root / args.grid_dir).resolve() if args.grid_dir else None
    if grid_dir is not None and not grid_dir.is_dir():
        raise FileNotFoundError(grid_dir)
    for unit in units:
        _prompt, paths, duration = unit_inputs(story_root, markdown, unit, grid_dir)
        print(
            f"PREFLIGHT unit={unit} images={len(paths)} duration={duration} "
            f"ratio=9:16 resolution={args.resolution}",
            flush=True,
        )
    print("BUDGET unknown", flush=True)
    return 0


def submit(args: argparse.Namespace) -> int:
    token, configured_base_url = load_video_provider_config(
        "canvas_video", "CANVAS_VIDEO_API_TOKEN", DEFAULT_BASE_URL
    )
    base_url = (args.base_url or configured_base_url).rstrip("/")
    story_root = Path(args.story_root).resolve()
    markdown_path = story_root / args.prompt_file
    markdown = markdown_path.read_text(encoding="utf-8")
    units = parse_units(args.units)
    grid_dir = (story_root / args.grid_dir).resolve() if args.grid_dir else None
    if grid_dir is not None and not grid_dir.is_dir():
        raise FileNotFoundError(grid_dir)
    ready = bool(re.search(r"(?m)^(?:fenjing_ready|grids_ready):\s*true\s*$", markdown))
    if not ready and not args.allow_partial_grids:
        raise RuntimeError("fenjing_ready 不是 true（老集为 grids_ready）；仅用户明确点名放行部分单元时可加 --allow-partial-grids")
    require_current_video_model(base_url, token, args.model)
    ledger = Path(args.ledger).resolve()
    if ledger.exists() and ledger.stat().st_size:
        raise FileExistsError(f"账本已存在且非空，拒绝重复提交: {ledger}")
    ledger.parent.mkdir(parents=True, exist_ok=True)

    prepared: dict[int, dict] = {}
    for unit in units:
        payload, paths = unit_payload(story_root, markdown, unit, args.model, args.resolution, grid_dir)
        prepared[unit] = payload
        print(f"PREFLIGHT unit={unit} images={len(paths)}", flush=True)

    lock = threading.Lock()
    errors = 0

    def one(unit: int) -> dict:
        response = api_json(
            base_url + "/v1/videos/generations",
            token,
            method="POST",
            payload=prepared[unit],
        )
        task_id = pick(response, "task_id", "taskId", "id")
        if not task_id:
            raise RuntimeError(f"单元{unit}响应缺少 task_id")
        entry = {
            "platform": "canvas",
            "unit": unit,
            "model": args.model,
            "task_id": str(task_id),
            "charged_cents": pick(response, "charged_cents", "chargedCents"),
            "balance_cents": pick(response, "balance_cents", "balanceCents"),
            "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "status": "submitted",
        }
        append_ledger(ledger, entry, lock)
        return entry

    with ThreadPoolExecutor(max_workers=min(args.concurrency, len(units))) as pool:
        futures = {pool.submit(one, unit): unit for unit in units}
        for future in as_completed(futures):
            unit = futures[future]
            try:
                entry = future.result()
                print(
                    f"SUBMITTED unit={unit} task_id={entry['task_id']} charged_cents={entry['charged_cents']}",
                    flush=True,
                )
            except Exception as exc:
                errors += 1
                print(f"ERROR unit={unit} {exc}", file=sys.stderr, flush=True)
    return 1 if errors else 0


def load_ledger(path: Path) -> list[dict]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


def poll(args: argparse.Namespace) -> int:
    token, configured_base_url = load_video_provider_config(
        "canvas_video", "CANVAS_VIDEO_API_TOKEN", DEFAULT_BASE_URL
    )
    base_url = (args.base_url or configured_base_url).rstrip("/")
    ledger = Path(args.ledger).resolve()
    entries = load_ledger(ledger)
    pending = {e["unit"]: str(e["task_id"]) for e in entries}
    failed_counts = {unit: 0 for unit in pending}
    story_root = Path(args.story_root).resolve()
    # 成片跟随分镜文件所在目录（模式子文件夹）；根目录分镜则落 story_root/成片（老集兼容）
    output_dir = (story_root / args.prompt_file).parent / "成片"
    output_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + args.timeout
    final_errors: dict[int | float, str] = {}

    while pending and time.time() < deadline:
        def check(item: tuple[int, str]):
            unit, task_id = item
            try:
                response = api_json(base_url + f"/v1/videos/{task_id}", token, timeout=30)
            except RuntimeError as exc:
                return unit, task_id, "query_error", None, str(exc)
            data = response.get("data") if isinstance(response.get("data"), dict) else response
            status = str(data.get("status", "")).lower()
            videos = data.get("videos") or []
            video_url = (
                data.get("video_url")
                or data.get("result_url")
                or data.get("resultVideoUrl")
                or data.get("url")
            )
            if videos and isinstance(videos[0], dict):
                video_url = video_url or videos[0].get("videoUrl") or videos[0].get("video_url") or videos[0].get("url")
            reason = data.get("failReason") or data.get("fail_reason") or data.get("error")
            return unit, task_id, status, video_url, reason

        with ThreadPoolExecutor(max_workers=min(args.concurrency, len(pending))) as pool:
            results = list(pool.map(check, list(pending.items())))
        for unit, task_id, status, video_url, reason in results:
            print(f"STATUS unit={unit} status={status or 'unknown'}", flush=True)
            if status == "succeeded" and video_url:
                target = output_dir / f"{unit}单元{args.output_suffix}.mp4"
                if target.exists() and target.stat().st_size:
                    print(f"EXISTS unit={unit} path={target}", flush=True)
                else:
                    temp = target.with_suffix(target.suffix + ".part")
                    urllib.request.urlretrieve(video_url, temp)
                    if not temp.is_file() or temp.stat().st_size == 0:
                        raise RuntimeError(f"单元{unit}下载结果为空")
                    os.replace(temp, target)
                    print(f"DOWNLOADED unit={unit} bytes={target.stat().st_size}", flush=True)
                append_ledger(
                    ledger,
                    {
                        "platform": "canvas",
                        "unit": unit,
                        "task_id": task_id,
                        "status": "succeeded",
                        "output_path": str(target),
                        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    },
                    threading.Lock(),
                )
                pending.pop(unit, None)
            elif status == "failed":
                failed_counts[unit] += 1
                if reason or failed_counts[unit] >= 3:
                    final_reason = str(reason or "连续3次 failed")
                    final_errors[unit] = final_reason
                    append_ledger(
                        ledger,
                        {
                            "platform": "canvas",
                            "unit": unit,
                            "task_id": task_id,
                            "status": "failed",
                            "fail_reason": final_reason,
                            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        },
                        threading.Lock(),
                    )
                    pending.pop(unit, None)
            else:
                failed_counts[unit] = 0
        if pending:
            time.sleep(args.interval)

    for unit in pending:
        final_errors[unit] = "轮询超时"
    if final_errors:
        for unit, reason in sorted(final_errors.items()):
            print(f"FINAL_ERROR unit={unit} reason={reason}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["preflight", "submit", "poll"])
    parser.add_argument("--story-root", required=True)
    parser.add_argument("--ledger", default="")
    parser.add_argument("--units", default="")
    parser.add_argument("--base-url", default=None, help="覆盖统一 API 配置中的 base_url")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt-file", default="分镜-30秒/分镜提示词.md", help="相对故事目录的分镜提示词路径；历史分镜请明确传原路径")
    parser.add_argument("--grid-dir", default=None, help="相对故事目录的宫格版本目录")
    parser.add_argument("--output-suffix", default="")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--interval", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--allow-partial-grids", action="store_true")
    args = parser.parse_args()
    if args.mode in {"preflight", "submit"} and not args.units:
        parser.error(f"{args.mode} 需要 --units")
    if args.mode in {"preflight", "submit"} and not args.model:
        parser.error(f"{args.mode} 需要 --model，模型必须按当前批次与用户确认")
    if args.mode in {"submit", "poll"} and not args.ledger:
        parser.error(f"{args.mode} 需要 --ledger")
    if args.mode == "preflight":
        return preflight(args)
    return submit(args) if args.mode == "submit" else poll(args)


if __name__ == "__main__":
    raise SystemExit(main())
