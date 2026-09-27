#!/usr/bin/env python3
"""Submit and collect Guagu OpenAPI video jobs using the unified API config."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from canvas_video_batch import (
    clean_mount_token,
    load_video_provider_config,
    mount_line,
    mount_tokens,
    parse_units,
    resolve_grid_mount,
    resolve_mount as canvas_resolve_mount,
    unit_section,
)


DEFAULT_BASE_URL = "https://guagu.cc"


def video_prompt(section: str, unit: int) -> str:
    """Read the authored prompt verbatim; Guagu accepts the mounted three-view assets."""
    # 现行契约为「### 单元提示词」（2026-07-23 宫格法废止）；老集宫格法文件回退「### 宫格转视频提示词」
    match = re.search(
        r"(?ms)^### 单元提示词\s*\n(?:```|~~~)(?:\w+)?\s*\n(.*?)\n(?:```|~~~)", section
    ) or re.search(
        r"(?ms)^### 宫格转视频提示词\s*\n(?:```|~~~)(?:\w+)?\s*\n(.*?)\n(?:```|~~~)", section
    )
    if not match:
        raise ValueError(f"单元{unit}未找到单元提示词（老集回退：宫格转视频提示词）")
    prompt = match.group(1).strip()
    forbidden_metadata = (
        "```",
        "修复记录：",
        "task_id",
        "任务号",
        "审核失败",
        "被拒绝并退款",
        "内容或参数不被支持",
    )
    leaked = [marker for marker in forbidden_metadata if marker in prompt]
    if leaked:
        raise ValueError(f"单元{unit}提示词混入代码块外元数据: {leaked}")
    return prompt


def resolve_mount(root: Path, token: str, unit: int | None = None, grid_dir: Path | None = None) -> Path:
    """Prefer the current three-view character masters for Guagu multi-reference jobs."""
    token = clean_mount_token(token)
    if "彩铅宫格" in token:
        if unit is None:
            raise ValueError("彩铅宫格需要当前单元号")
        return root / "视频参考图" / f"单元{unit}-宫格-彩铅参考.png"
    if "S1彩铅锚点A" in token or "S1锚点A彩铅" in token:
        return root / "视频参考图" / "S1-社区面馆门口-锚点A-祖孙登门-v2-彩铅参考.png"
    cinematic_video_refs = {
        "R1电影写实三视图": "R1-林小禾-电影写实面部特写全身三视图.png",
        "R2电影写实三视图": "R2-禾子奶奶-电影写实面部特写全身三视图.png",
        "R3电影写实三视图": "R3-陈玲-电影写实面部特写全身三视图.png",
    }
    for label, filename in cinematic_video_refs.items():
        if label in token:
            return root / "视频参考图" / filename
    if "本单元宫格图" in token:
        if unit is None:
            raise ValueError("本单元宫格图需要当前单元号")
        return resolve_grid_mount(root, unit, grid_dir)
    if "单元16写实宫格-v2全脸网格" in token:
        return root / "宫格图" / "15S重构版" / "单元16-写实宫格-v2-全脸网格.png"
    if "单元16写实宫格-v1" in token:
        return root / "宫格图" / "15S重构版" / "单元16-写实宫格-v1.png"
    if re.search(r"单元16宫格图", token):
        return root / "宫格图" / "15S重构版" / "单元16-宫格-v2.png"
    grid = re.search(r"单元(\d+)宫格图", token)
    if grid and grid_dir is not None:
        return resolve_grid_mount(root, int(grid.group(1)), grid_dir)
    three_view_mappings = [
        ("R1a婆婆日常三视图", root / "资产图" / "R1-婆婆-日常-三视图.png"),
        ("R2a大伟家居三视图", root / "资产图" / "R2-大伟-家居服-三视图.png"),
        ("R2b大伟外套三视图", root / "资产图" / "R2-大伟-外套-三视图.png"),
        ("R4王磊日常素描头参考图", root / "资产图" / "R4-王磊-日常-素描头参考图.jpeg"),
    ]
    for label, path in three_view_mappings:
        if label in token:
            return path
    return canvas_resolve_mount(root, token, unit, grid_dir)


def unwrap(response: requests.Response) -> object:
    response.raise_for_status()
    obj = response.json()
    if obj.get("code") != 0:
        raise RuntimeError(f"API code={obj.get('code')}: {obj.get('message')}")
    return obj.get("data")


def session_for(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
    return session


def get_with_transient_retry(session: requests.Session, url: str, timeout: int = 30) -> object:
    for attempt in range(1, 6):
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            return unwrap(response)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 5:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET重试耗尽: {url}")


def append_ledger(path: Path, entry: dict, lock: threading.Lock) -> None:
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    with lock:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def prepare_units(
    story_root: Path,
    units: list[int],
    prompt_file: str,
    grid_dir: str | None,
    allow_partial_grids: bool,
    resolution: str = "720p",
) -> dict[int, dict]:
    markdown_path = story_root / prompt_file
    markdown = markdown_path.read_text(encoding="utf-8")
    ready = bool(re.search(r"(?m)^(?:fenjing_ready|grids_ready):\s*true\s*$", markdown))
    if not ready and not allow_partial_grids:
        raise RuntimeError("fenjing_ready 不是 true（老集为 grids_ready）；仅用户明确点名放行部分单元时可加 --allow-partial-grids")
    resolved_grid_dir = (story_root / grid_dir).resolve() if grid_dir else None
    if resolved_grid_dir is not None and not resolved_grid_dir.is_dir():
        raise FileNotFoundError(resolved_grid_dir)
    prepared: dict[int, dict] = {}
    for unit in units:
        section = unit_section(markdown, unit)
        prompt = video_prompt(section, unit)
        tokens = mount_tokens(section, unit)
        paths = [resolve_mount(story_root, token, unit, resolved_grid_dir) for token in tokens]
        for path in paths:
            if not path.is_file() or path.stat().st_size == 0:
                raise FileNotFoundError(path)
            if path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError(f"参考素材超过20MB: {path}")
        duration_match = re.search(r"(?m)^生成参数：\s*(\d+)秒", section)
        if not duration_match:
            raise ValueError(f"单元{unit}未找到生成参数时长行")
        duration = int(duration_match.group(1))
        prepared[unit] = {"prompt": prompt, "paths": paths, "duration": duration}
        print(f"PREFLIGHT unit={unit} images={len(paths)} duration={duration} ratio=9:16 resolution={resolution}", flush=True)
    return prepared


def upload_reference(session: requests.Session, base_url: str, path: Path) -> dict:
    header = path.read_bytes()[:12]
    if header.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
        upload_name = path.with_suffix(".jpg").name
    elif header.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
        upload_name = path.with_suffix(".png").name
    elif header.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
        upload_name = path.with_suffix(".gif").name
    else:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        upload_name = path.name
    for attempt in range(1, 6):
        try:
            with path.open("rb") as handle:
                response = session.post(
                    base_url + "/openapi/v1/video-generations/references",
                    files={"file": (upload_name, handle, mime)},
                    data={"kind": "image"},
                    timeout=180,
                )
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            if response.status_code >= 400:
                raise RuntimeError(
                    f"素材上传失败 file={path.name} status={response.status_code} body={response.text[:500]}"
                )
            data = unwrap(response)
            if not isinstance(data, dict) or not str(data.get("url", "")).startswith("asset://"):
                raise RuntimeError(f"素材上传响应无有效句柄: {data}")
            return data
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 5:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"素材上传重试耗尽: {path}")


def recover_by_request_id(session: requests.Session, base_url: str, request_ids: set[str]) -> dict[str, dict]:
    response = session.get(
        base_url + "/openapi/v1/video-generations",
        params={"page": 1, "page_size": 100},
        timeout=60,
    )
    data = unwrap(response)
    if isinstance(data, dict):
        rows = data.get("items") or data.get("list") or data.get("records") or []
    else:
        rows = data or []
    return {
        str(row.get("request_id")): row
        for row in rows
        if isinstance(row, dict) and str(row.get("request_id", "")) in request_ids
    }


def require_current_model(
    args: argparse.Namespace,
    session: requests.Session,
    base_url: str,
) -> dict:
    models = get_with_transient_retry(
        session, base_url + "/openapi/v1/video-generations/models", timeout=30
    )
    selected = [
        row for row in models
        if row.get("model") == args.model
        and (not args.resolution or row.get("resolution") == args.resolution)
        and row.get("type") == "image_to_video"
    ]
    if len(selected) != 1:
        raise RuntimeError(
            f"当前模型表无法唯一匹配已确认的 image_to_video 模型 {args.model}"
            f"（分辨率：{args.resolution or '按实时模型表'}），停止操作"
        )
    model = selected[0]
    if not isinstance(model.get("resolution"), str) or not model["resolution"].strip():
        raise RuntimeError("当前模型表没有返回可确认的 resolution，停止操作")
    print(
        f"MODEL model={args.model} resolution={model['resolution']} cost={model.get('credit_cost')} producible={model.get('producible')}",
        flush=True,
    )
    return model


def preflight(args: argparse.Namespace) -> int:
    token, configured_base_url = load_video_provider_config(
        "guagu_video", "GUAGU_VIDEO_API_KEY", DEFAULT_BASE_URL
    )
    base_url = (args.base_url or configured_base_url).rstrip("/")
    session = session_for(token)
    model = require_current_model(args, session, base_url)
    story_root = Path(args.story_root).resolve()
    units = parse_units(args.units)
    prepared = prepare_units(
        story_root,
        units,
        args.prompt_file,
        args.grid_dir,
        args.allow_partial_grids,
        resolution=model["resolution"],
    )
    if args.duration is not None:
        if args.duration <= 0:
            raise ValueError("--duration 必须为正整数")
        for item in prepared.values():
            item["duration"] = args.duration
    unit_cost = model.get("credit_cost")
    if not isinstance(unit_cost, (int, float)):
        raise RuntimeError("当前模型表没有返回可确认的 credit_cost，停止操作")
    total_cost = round(float(unit_cost) * len(units), 6)
    print(
        f"BUDGET units={len(units)} unit_cost={unit_cost} total={total_cost}",
        flush=True,
    )
    return 0


def submit(args: argparse.Namespace) -> int:
    token, configured_base_url = load_video_provider_config(
        "guagu_video", "GUAGU_VIDEO_API_KEY", DEFAULT_BASE_URL
    )
    base_url = (args.base_url or configured_base_url).rstrip("/")
    session = session_for(token)
    model = require_current_model(args, session, base_url)

    story_root = Path(args.story_root).resolve()
    units = parse_units(args.units)
    ledger = Path(args.ledger).resolve()
    if ledger.exists() and ledger.stat().st_size:
        raise FileExistsError(f"账本已存在且非空，拒绝重复提交: {ledger}")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    prepared = prepare_units(
        story_root,
        units,
        args.prompt_file,
        args.grid_dir,
        args.allow_partial_grids,
        resolution=model["resolution"],
    )
    if args.duration is not None:
        if args.duration <= 0:
            raise ValueError("--duration 必须为正整数")
        for item in prepared.values():
            item["duration"] = args.duration
        print(f"DURATION_OVERRIDE duration={args.duration}s units={units}", flush=True)

    unique_paths = sorted({path for item in prepared.values() for path in item["paths"]}, key=str)
    assets: dict[Path, dict] = {}
    # The current Guagu protocol recommends at most two concurrent uploads.
    with ThreadPoolExecutor(max_workers=min(args.concurrency, 2, len(unique_paths))) as pool:
        futures = {pool.submit(upload_reference, session_for(token), base_url, path): path for path in unique_paths}
        for future in as_completed(futures):
            path = futures[future]
            assets[path] = future.result()
            print(f"UPLOADED file={path.name} asset={assets[path]['url']}", flush=True)

    lock = threading.Lock()
    errors: dict[int, str] = {}
    story_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", story_root.name).strip("-") or "story"
    request_ids = {
        unit: f"{story_slug}-unit{unit}-{args.model}-{model['resolution']}-{args.version}"
        for unit in units
    }

    def one(unit: int) -> dict:
        item = prepared[unit]
        payload = {
            "model": args.model,
            "prompt": item["prompt"],
            "resolution": model["resolution"],
            "ratio": "9:16",
            "duration": item["duration"],
            "generate_audio": True,
            "content": [
                {"type": "image_url", "role": "reference_image", "image_url": {"url": assets[path]["url"]}}
                for path in item["paths"]
            ],
        }
        worker = session_for(token)
        response = worker.post(
            base_url + "/openapi/v1/video-generations",
            headers={"Idempotency-Key": request_ids[unit]},
            json=payload,
            timeout=240,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"创建任务失败 status={response.status_code} body={response.text[:1000]}"
            )
        data = unwrap(response)
        entry = {
            "platform": "guagu",
            "unit": unit,
            "model": args.model,
            "resolution": model["resolution"],
            "task_id": str(data["id"]),
            "request_id": str(data.get("request_id") or request_ids[unit]),
            "estimated_cost": data.get("estimated_cost"),
            "billing_status": data.get("billing_status"),
            "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "last_status": str(data.get("status") or "queued"),
        }
        append_ledger(ledger, entry, lock)
        return entry

    with ThreadPoolExecutor(max_workers=min(args.concurrency, len(units))) as pool:
        futures = {pool.submit(one, unit): unit for unit in units}
        for future in as_completed(futures):
            unit = futures[future]
            try:
                entry = future.result()
                print(f"SUBMITTED unit={unit} task_id={entry['task_id']} estimated_cost={entry['estimated_cost']}", flush=True)
            except Exception as exc:
                errors[unit] = str(exc)
                print(f"SUBMIT_UNCERTAIN unit={unit} {exc}", file=sys.stderr, flush=True)

    if errors:
        recovered = recover_by_request_id(session, base_url, {request_ids[unit] for unit in errors})
        for unit in list(errors):
            row = recovered.get(request_ids[unit])
            if row and row.get("id"):
                entry = {
                    "platform": "guagu",
                    "unit": unit,
                    "model": args.model,
                    "resolution": model["resolution"],
                    "task_id": str(row["id"]),
                    "request_id": request_ids[unit],
                    "estimated_cost": row.get("estimated_cost"),
                    "billing_status": row.get("billing_status"),
                    "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "last_status": str(row.get("status") or "queued"),
                    "recovered": True,
                }
                append_ledger(ledger, entry, lock)
                print(f"RECOVERED unit={unit} task_id={entry['task_id']}", flush=True)
                errors.pop(unit)
    for unit, error in sorted(errors.items()):
        print(f"FINAL_SUBMIT_ERROR unit={unit} {error}", file=sys.stderr)
    return 1 if errors else 0


def load_ledger(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def poll(args: argparse.Namespace) -> int:
    token, configured_base_url = load_video_provider_config(
        "guagu_video", "GUAGU_VIDEO_API_KEY", DEFAULT_BASE_URL
    )
    session = session_for(token)
    base_url = (args.base_url or configured_base_url).rstrip("/")
    entries = load_ledger(Path(args.ledger).resolve())
    pending: dict[int, str] = {}
    for row in entries:
        unit = int(row["unit"])
        status = str(row.get("last_status") or row.get("status") or "")
        output_file = row.get("output_file")
        if status == "succeeded" and output_file:
            output_path = Path(output_file)
            if output_path.is_file() and output_path.stat().st_size > 0:
                pending.pop(unit, None)
                continue
        if status in {"failed", "canceled"}:
            pending.pop(unit, None)
            continue
        if row.get("task_id"):
            pending[unit] = str(row["task_id"])
    story_root = Path(args.story_root).resolve()
    # 成片跟随分镜文件所在目录（模式子文件夹）；根目录分镜则落 story_root/成片（老集兼容）
    output_dir = (story_root / args.prompt_file).parent / "成片"
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = Path(args.ledger).resolve()
    ledger_lock = threading.Lock()
    deadline = time.time() + args.timeout
    errors: dict[int, str] = {}
    while pending and time.time() < deadline:
        for unit, task_id in list(pending.items()):
            data = get_with_transient_retry(
                session,
                base_url + f"/openapi/v1/video-generations/{task_id}",
                timeout=30,
            )
            status = str(data.get("status", ""))
            print(f"STATUS unit={unit} status={status} progress={data.get('progress')}", flush=True)
            if status == "succeeded":
                url = (data.get("result") or {}).get("video_url")
                if not url:
                    errors[unit] = "成功状态缺少 video_url"
                else:
                    target = output_dir / f"{unit}单元{args.output_suffix}-原始.mp4"
                    temp = target.with_suffix(target.suffix + ".part")
                    download_error = None
                    for attempt in range(1, 6):
                        try:
                            with session.get(url, stream=True, timeout=180) as response:
                                response.raise_for_status()
                                with temp.open("wb") as handle:
                                    for chunk in response.iter_content(1024 * 1024):
                                        if chunk:
                                            handle.write(chunk)
                            download_error = None
                            break
                        except (requests.RequestException, OSError) as exc:
                            download_error = str(exc)
                            temp.unlink(missing_ok=True)
                            print(
                                f"DOWNLOAD_RETRY unit={unit} attempt={attempt}/5 error={exc}",
                                file=sys.stderr,
                                flush=True,
                            )
                            if attempt < 5:
                                time.sleep(2 ** attempt)
                    if download_error is not None:
                        errors[unit] = f"下载失败（已重试5次）: {download_error}"
                        continue
                    if not temp.is_file() or temp.stat().st_size == 0:
                        errors[unit] = "下载结果为空"
                        continue
                    else:
                        os.replace(temp, target)
                        append_ledger(
                            ledger_path,
                            {
                                "platform": "guagu",
                                "unit": unit,
                                "model": args.model,
                                "task_id": task_id,
                                "last_status": "succeeded",
                                "final_cost": data.get("final_cost"),
                                "billing_status": data.get("billing_status"),
                                "output_file": str(target),
                                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            },
                            ledger_lock,
                        )
                        print(f"DOWNLOADED unit={unit} bytes={target.stat().st_size} final_cost={data.get('final_cost')}", flush=True)
                pending.pop(unit)
            elif status in {"failed", "canceled"}:
                errors[unit] = str(data.get("fail_reason") or status)
                append_ledger(
                    ledger_path,
                    {
                        "platform": "guagu",
                        "unit": unit,
                        "model": args.model,
                        "task_id": task_id,
                        "last_status": status,
                        "final_cost": data.get("final_cost"),
                        "billing_status": data.get("billing_status"),
                        "fail_reason": data.get("fail_reason"),
                        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    },
                    ledger_lock,
                )
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
    parser.add_argument("--base-url", default=None, help="覆盖统一 API 配置中的 base_url")
    parser.add_argument("--model", default="")
    parser.add_argument("--resolution", default=None, help="已确认模型的分辨率；省略时按实时模型表唯一匹配")
    parser.add_argument("--prompt-file", default="分镜-30秒/分镜提示词.md")
    parser.add_argument("--grid-dir", default=None, help="相对故事目录的宫格版本目录")
    parser.add_argument("--allow-partial-grids", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--interval", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--output-suffix", default="-v2")
    parser.add_argument("--version", default="v2")
    parser.add_argument("--duration", type=int, default=None, help="覆盖本批发送给 Guagu 的视频时长；需经用户明确确认")
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
