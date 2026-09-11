"""Shared local reference resolution and cached public-image uploads."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from canvas_video_batch import (
    mount_tokens as canvas_mount_tokens,
    resolve_mount as canvas_resolve_mount,
)


IMGBB_UPLOAD_URL = "https://api.imgbb.com/1/upload"
CONFIG_PATH = Path(__file__).with_name("api配置.json")


def config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))


def imgbb_key() -> str:
    key = str(config().get("imgbb", {}).get("api_key") or "")
    if not key:
        raise RuntimeError("api配置.json 缺少 imgbb.api_key")
    return key


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return {}


def save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def imgbb_upload(path: Path, key: str, cache: dict, cache_path: Path) -> str:
    digest = sha256_of(path)
    if digest in cache:
        return cache[digest]
    boundary = "----videoReferenceBoundary" + hashlib.md5(os.urandom(8)).hexdigest()
    data = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        f"{IMGBB_UPLOAD_URL}?key={urllib.parse.quote(key)}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    url = (payload.get("data") or {}).get("url")
    if not url:
        raise RuntimeError(f"imgbb 上传未返回 url: {payload}")
    cache[digest] = url
    save_cache(cache_path, cache)
    print(f"IMGBB_UPLOADED {path.name}", flush=True)
    return url


def mount_tokens(section: str, unit: int) -> list[str]:
    """Keep Markdown link paths intact; retain legacy plain-name mounts."""
    match = re.search(r"(?m)^视频挂载：\s*(.+)$", section)
    if not match:
        raise ValueError(f"单元{unit}未找到视频挂载")
    line = match.group(1).strip().rstrip("。.")
    if re.search(r"\[[^\]]+\]\(", line):
        # Only separators outside a Markdown destination separate uploads.
        parts = re.split(r"\s*(?:/|[；;])\s*(?=第\d+个上传)", line)
        if not all(re.match(r"第\d+个上传", part) for part in parts):
            raise ValueError(f"单元{unit}带链接的挂载缺少上传序号")
        numbers = [int(re.match(r"第(\d+)个上传", part).group(1)) for part in parts]
        if numbers != list(range(1, len(parts) + 1)):
            raise ValueError(f"单元{unit}上传序号不连续: {numbers}")
        return parts
    return canvas_mount_tokens(section, unit)


def resolve_asset(story_dir: Path, token: str, unit: int) -> Path:
    link = re.search(r"\[[^\]]+\]\(\s*(?:<([^>]+)>|([^\r\n]+?))\s*\)\s*$", token)
    if link:
        target = (link.group(1) or link.group(2)).strip()
        path = Path(target)
        if not path.is_absolute():
            path = story_dir / path
        if not path.is_file():
            raise FileNotFoundError(path)
        return path.resolve()
    return canvas_resolve_mount(story_dir, token, unit)
