"""Submit one finished storyboard to the Wanwan slow queue without blind retries.

Usage:
  python -X utf8 工具/wanwan_video_batch.py prepare STORY_DIR
  python -X utf8 工具/wanwan_video_batch.py submit STORY_DIR
  python -X utf8 工具/wanwan_video_batch.py poll STORY_DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
BASE = "https://wanwan.coolhs.com"
CARD = Path("<用户目录>/.codex/runtime/credentials/wanwan-video.txt")
BAD_PROMPT_TEXT = ("```", "修复记录", "退款", "审核", "task_id")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"at": datetime.now(timezone.utc).isoformat(), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def journal_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def story_paths(story_dir: Path) -> tuple[Path, Path, Path]:
    storyboard = story_dir / "分镜-30秒" / "分镜提示词.md"
    task_dir = ROOT / "工具" / "运行垃圾" / "tasks" / story_dir.name
    return storyboard, task_dir / "wanwan-提交账本.jsonl", task_dir / "wanwan-素材缓存.json"


def parse_story(story_dir: Path, prompt_file: Path | None = None) -> list[dict]:
    storyboard, _, _ = story_paths(story_dir)
    if prompt_file is not None:
        storyboard = prompt_file
    source = storyboard.read_text(encoding="utf-8")
    header = source.split("---", 2)[1]
    if not re.search(r"^fenjing_ready:\s*true\s*$", header, re.M):
        raise ValueError("fenjing_ready 不是 true")
    if not re.search(r"^mode:\s*30秒\s*$", header, re.M):
        raise ValueError("不是 30 秒分镜")
    expected = re.search(r"^单元数:\s*(\d+)\s*$", header, re.M)
    if not expected:
        raise ValueError("缺少单元数")
    starts = list(re.finditer(r"^## 单元(\d+)（", source, re.M))
    if len(starts) != int(expected.group(1)):
        raise ValueError("单元标题数量与 frontmatter 不一致")
    units = []
    for index, match in enumerate(starts):
        number = int(match.group(1))
        if number != index + 1:
            raise ValueError("单元编号不连续")
        section = source[match.start() : starts[index + 1].start() if index + 1 < len(starts) else len(source)]
        mounts = re.findall(r"^- 图片(\d+)＝\.\./资产图/([^（\r\n]+)（", section, re.M)
        if not mounts:
            mounts = re.findall(r"^- 图片(\d+)：[^\r\n]*\]\(\.\./资产图/([^)\r\n]+)\)\s*$", section, re.M)
        if not mounts:
            mount_line = re.search(r"^视频挂载：([^\r\n]+)$", section, re.M)
            if mount_line:
                mounts = re.findall(r"(?:^|[；;]\s*)图片(\d+)\s*[=＝]\s*([^；;\r\n]+)", mount_line.group(1))
                mounts = [(n, filename.strip().removeprefix("../资产图/")) for n, filename in mounts]
        if not mounts or len(mounts) > 9 or [int(n) for n, _ in mounts] != list(range(1, len(mounts) + 1)):
            raise ValueError(f"单元{number}挂载编号或数量错误")
        if not re.search(r"^生成参数：30秒，9:16，", section, re.M):
            raise ValueError(f"单元{number}参数不是30秒9:16")
        block = re.search(r"### 单元提示词\s*\n\s*```(?:text)?\s*\n([\s\S]*?)\n```", section)
        if not block:
            raise ValueError(f"单元{number}代码块缺失")
        prompt = block.group(1).strip()
        bindings = prompt.split("镜头1", 1)[0]
        if not re.search(r"参考图1|图1中的", bindings) or not prompt.removesuffix("不生成字幕或水印。").rstrip().endswith("全程不要生成背景音乐（BGM），仅生成人物对白和提示词明确指定的动作音效。"):
            raise ValueError(f"单元{number}提示词首尾越界")
        if any(term in prompt for term in BAD_PROMPT_TEXT) or len(prompt) > 20000:
            raise ValueError(f"单元{number}提示词含越界内容或超长")
        referenced = [int(x) for x in re.findall(r"图(\d+)", prompt)]
        if referenced and max(referenced) > len(mounts):
            raise ValueError(f"单元{number}引用不存在的图片")
        files = []
        for _, filename in mounts:
            image = story_dir / "资产图" / filename
            if not image.is_file() or image.stat().st_size == 0 or image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise ValueError(f"单元{number}挂载文件缺失或格式不受支持：{filename}")
            files.append(image)
        units.append({"number": number, "prompt": prompt, "files": files, "hash": sha256(prompt.encode("utf-8"))})
    return units


def session() -> requests.Session:
    match = re.search(r"^KEY\s*[:=]\s*(.+)$", CARD.read_text(encoding="utf-8"), re.M)
    if not match:
        raise ValueError("万万卡密文件缺少 KEY")
    client = requests.Session()
    client.headers.update({"Authorization": "Bearer " + match.group(1).strip(), "User-Agent": "Mozilla/5.0"})
    return client


def get_json(client: requests.Session, path: str) -> tuple[int, dict]:
    response = client.get(BASE + path, timeout=30)
    try:
        data = response.json()
    except ValueError:
        data = {"body": response.text[:200]}
    return response.status_code, data if isinstance(data, dict) else {"data": data}


def remote_preflight(client: requests.Session, to_submit: int) -> dict:
    status, models = get_json(client, "/v1/models")
    if status != 200 or "wan-1080p" not in [x.get("id") for x in models.get("data", [])]:
        raise RuntimeError("实时模型目录没有 wan-1080p")
    status, ride = get_json(client, "/v1/ride")
    if status != 200 or not ride.get("accepting"):
        raise RuntimeError("万万慢速渠道当前不收单")
    status, card = get_json(client, "/api/portal/me")
    if status != 200 or card.get("maintenance") or card.get("resolution") != "1080P":
        raise RuntimeError("卡密状态或1080P权限不符合提交条件")
    if card.get("unit") != "次":
        raise RuntimeError("卡密计量单位已变化，需重新核算费用")
    if not card.get("unlimited") and int(card.get("remaining", 0)) < to_submit:
        raise RuntimeError(f"剩余额度{card.get('remaining')}次，待提交{to_submit}次，不足")
    return {"remaining": card.get("remaining"), "unit": card.get("unit"), "accepting": ride.get("accepting"), "to_submit": to_submit}


def media_id_from_response(data: dict) -> str | None:
    for obj in (data, data.get("item"), data.get("media"), data.get("data")):
        if isinstance(obj, dict):
            for field in ("id", "mid", "mediaId"):
                value = obj.get(field)
                if isinstance(value, str) and value:
                    return value
    return None


def upload_media(client: requests.Session, files: list[Path], cache_path: Path) -> dict[str, str]:
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    mapping = {}
    for file in files:
        digest = sha256(file.read_bytes())
        if digest in cache:
            mapping[str(file)] = cache[digest]["id"]
            continue
        with file.open("rb") as handle:
            try:
                response = client.post(BASE + "/api/portal/media", data={"kind": "image"}, files={"file": (file.name, handle)}, timeout=120)
            except requests.RequestException as error:
                raise RuntimeError(f"素材上传结果不明：{file.name}；先查远端媒体库，勿盲重传") from error
        if response.status_code != 200:
            raise RuntimeError(f"素材上传失败：{file.name}，HTTP {response.status_code}，{response.text[:160]}")
        media_id = media_id_from_response(response.json())
        if not media_id:
            raise RuntimeError(f"素材上传已返回成功但未识别回执ID：{file.name}；先查远端媒体库")
        cache[digest] = {"id": media_id, "file": file.name}
        save_json(cache_path, cache)
        mapping[str(file)] = media_id
        print(f"MEDIA {file.name} -> {media_id}", flush=True)
    return mapping


def submit(story_dir: Path) -> None:
    units = parse_story(story_dir)
    _, journal_path, cache_path = story_paths(story_dir)
    past = journal_events(journal_path)
    attempted = {e["unit"] for e in past if e.get("event") in {"attempt", "submitted", "unknown_outcome", "existing"}}
    remaining = [u for u in units if u["number"] not in attempted]
    if len(remaining) != len(units):
        raise RuntimeError("账本已有提交尝试；先 poll/对账，不自动重投，也不把部分批次误称全量")
    with session() as client:
        info = remote_preflight(client, len(remaining))
        print("PREFLIGHT " + json.dumps(info, ensure_ascii=False), flush=True)
        files = list(dict.fromkeys(file for u in remaining for file in u["files"]))
        media_ids = upload_media(client, files, cache_path)
        for unit in remaining:
            number = unit["number"]
            media = [{"id": media_ids[str(file)], "kind": "image", "name": file.name, "token": f"图{index}", "role": "ref"}
                     for index, file in enumerate(unit["files"], 1)]
            identity = f"{story_dir.name}|{number}|{unit['hash']}|" + "|".join(x["id"] for x in media)
            job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
            state, existing = get_json(client, "/api/portal/jobs/" + job_id)
            if state == 200:
                append_event(journal_path, {"event": "existing", "unit": number, "id": job_id, "status": existing.get("status"), "prompt_sha256": unit["hash"]})
                print(f"EXISTING U{number:02d} {job_id}", flush=True)
                continue
            if state != 404:
                raise RuntimeError(f"U{number:02d}远端对账异常HTTP {state}，停止提交")
            payload = {"id": job_id, "prompt": unit["prompt"], "ratio": "9:16", "resolution": "1080P", "duration": 30,
                       "audio": True, "copies": 1, "media": media}
            append_event(journal_path, {"event": "attempt", "unit": number, "id": job_id,
                                        "prompt_sha256": unit["hash"], "media_ids": [x["id"] for x in media]})
            try:
                response = client.post(BASE + "/api/portal/jobs", json=payload, timeout=60)
            except requests.RequestException as error:
                append_event(journal_path, {"event": "unknown_outcome", "unit": number, "id": job_id, "error": str(error)[:160]})
                raise RuntimeError(f"U{number:02d}提交结果不明，整批停止；仅查询原ID {job_id}") from error
            try:
                data = response.json()
            except ValueError:
                data = {}
            if response.status_code != 200:
                append_event(journal_path, {"event": "submit_failed", "unit": number, "id": job_id,
                                            "http": response.status_code, "error": str(data.get("detail", ""))[:160]})
                raise RuntimeError(f"U{number:02d}提交返回HTTP {response.status_code}，整批停止；先对账")
            append_event(journal_path, {"event": "submitted", "unit": number, "id": job_id,
                                        "status": data.get("status"), "cost": data.get("cost")})
            print(f"SUBMITTED U{number:02d} {job_id} status={data.get('status')}", flush=True)


def poll(story_dir: Path) -> None:
    _, journal_path, _ = story_paths(story_dir)
    events = journal_events(journal_path)
    jobs = {e["unit"]: e["id"] for e in events if e.get("event") in {"attempt", "submitted", "existing"}}
    if not jobs:
        print("暂无本集任务账本")
        return
    with session() as client:
        for unit, job_id in sorted(jobs.items()):
            status, job = get_json(client, "/api/portal/jobs/" + job_id)
            print(json.dumps({"unit": unit, "id": job_id, "http": status,
                              "status": job.get("status"), "progress": job.get("progress"),
                              "wait": job.get("wait", {}).get("phase") if isinstance(job.get("wait"), dict) else None}, ensure_ascii=False))


def download(story_dir: Path) -> None:
    """Save only successful original jobs; never replace a local video."""
    _, journal_path, _ = story_paths(story_dir)
    events = journal_events(journal_path)
    jobs = {e["unit"]: e["id"] for e in events if e.get("event") in {"attempt", "submitted", "existing"}}
    output_dir = story_dir / "分镜-30秒" / "成片"
    output_dir.mkdir(parents=True, exist_ok=True)
    failed_downloads = []
    with session() as client:
        for unit, job_id in sorted(jobs.items()):
            status, job = get_json(client, "/api/portal/jobs/" + job_id)
            if status != 200:
                print(f"SKIP U{unit:02d} detail HTTP {status}", flush=True)
                failed_downloads.append(unit)
                continue
            if job.get("status") != "succeeded" or not job.get("hasOutput"):
                print(f"SKIP U{unit:02d} status={job.get('status')}", flush=True)
                continue
            final = output_dir / f"U{unit:02d}-万万-{job_id}.mp4"
            if final.exists():
                if final.stat().st_size == 0:
                    raise RuntimeError(f"既有成片是空文件，停止：{final}")
                print(f"EXISTING_VIDEO U{unit:02d} {final.name}", flush=True)
                continue
            partial = final.with_name(final.name + ".part")
            if partial.exists():
                raise RuntimeError(f"发现上次未完成下载，先核对：{partial}")
            try:
                response = client.get(BASE + "/v1/videos/" + job_id + "/content", stream=True, timeout=(20, 180))
                if response.status_code != 200 or not response.headers.get("content-type", "").startswith("video/"):
                    raise RuntimeError(f"HTTP {response.status_code}, type={response.headers.get('content-type')}")
                digest = hashlib.sha256()
                size = 0
                with partial.open("xb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                expected = int(response.headers.get("content-length", 0))
                if size < 1024 or (expected and size != expected):
                    raise RuntimeError(f"下载长度不符：{size}/{expected}")
                with partial.open("rb") as handle:
                    if b"ftyp" not in handle.read(16):
                        raise RuntimeError("下载结果没有 MP4 文件头")
                os.replace(partial, final)
                append_event(journal_path, {"event": "downloaded", "unit": unit, "id": job_id,
                                            "file": final.name, "bytes": size, "sha256": digest.hexdigest()})
                print(f"DOWNLOADED U{unit:02d} {final.name} {size} bytes", flush=True)
            except Exception as error:
                failed_downloads.append(unit)
                # Request exceptions may contain a signed download URL.
                print(f"DOWNLOAD_FAILED U{unit:02d} {type(error).__name__}", flush=True)
            finally:
                if "response" in locals():
                    response.close()
    if failed_downloads:
        raise RuntimeError("部分成功视频尚未落盘：" + ",".join(map(str, failed_downloads)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "submit", "poll", "download"))
    parser.add_argument("story_dir", type=Path)
    args = parser.parse_args()
    story_dir = args.story_dir.resolve()
    if args.command == "poll":
        poll(story_dir)
    elif args.command == "download":
        download(story_dir)
    elif args.command == "prepare":
        units = parse_story(story_dir)
        with session() as client:
            print(json.dumps({"units": len(units), "unique_media": len({f for u in units for f in u["files"]}),
                              **remote_preflight(client, len(units))}, ensure_ascii=False))
    else:
        submit(story_dir)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        sys.exit(1)
