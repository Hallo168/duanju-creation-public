#!/usr/bin/env python3
"""Dolasd reference-image video adapter. Creation is never retried automatically."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib import error, parse, request
import uuid

from video_provider_catalog import read_config, token_for, model_rows


PROVIDER = "dolasd_video"
ROOT = Path(__file__).resolve().parent.parent


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"at": now(), "provider": PROVIDER, **row}, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def ledger_state(path: Path) -> dict:
    if not path.exists():
        return {}
    state = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("provider") != PROVIDER:
                raise RuntimeError("Ledger belongs to another provider")
            state.update(row)
    return state


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self) -> None:
        self.provider = read_config()["providers"][PROVIDER]
        if self.provider.get("disabled") or self.provider.get("enabled") is False:
            raise RuntimeError("Provider is disabled")
        self.base = self.provider["base_url"].rstrip("/")
        self.token = token_for(self.provider)
        if not self.token:
            raise RuntimeError("Dolasd credential is missing")
        self.opener = request.build_opener(NoRedirect)

    def clean(self, value: object) -> str:
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return raw.replace(self.token, "[REDACTED]")

    def url(self, name: str, task_id: str = "") -> tuple[str, str]:
        method, path = self.provider["endpoints"][name].split(" ", 1)
        path = path.replace("{task_id}", parse.quote(str(task_id), safe=""))
        return method, self.base + path

    def call(self, name: str, payload: dict | None = None, blob: bytes | None = None,
             content_type: str | None = None, task_id: str = "", timeout: int = 45) -> tuple[int, object]:
        method, url = self.url(name, task_id)
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json", "User-Agent": "ShortDramaDolasd/1.0"}
        body = blob
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        try:
            response = self.opener.open(request.Request(url, data=body, headers=headers, method=method), timeout=timeout)
        except error.HTTPError as exc:
            response = exc
        with response:
            status = response.code
            raw = response.read(2_000_000).decode("utf-8", "replace")
        try:
            value = json.loads(self.clean(raw))
        except ValueError:
            value = {"message": self.clean(raw)[:1000]}
        return status, value


def live_check(client: Client, model_id: str, duration: int, size: str) -> dict:
    status, data = client.call("models")
    if status != 200:
        raise RuntimeError(f"Model catalogue HTTP {status}: {client.clean(data)[:500]}")
    model = next((row for row in model_rows(data) if isinstance(row, dict) and row.get("id") == model_id), None)
    if model is None:
        raise RuntimeError(f"Model {model_id} is absent from the live catalogue")
    durations = model.get("durations_all") or model.get("durations") or []
    sizes = model.get("sizes") or []
    if duration not in durations or size not in sizes:
        raise RuntimeError(f"Model does not list duration={duration}, size={size}")
    status, key_info = client.call("key_info")
    if status != 200 or not isinstance(key_info, dict):
        raise RuntimeError(f"Key status HTTP {status}: {client.clean(key_info)[:500]}")
    if key_info.get("enabled") is not True:
        raise RuntimeError("API key is not enabled")
    allowed = key_info.get("allowed_durations")
    if isinstance(allowed, list) and duration not in allowed:
        raise RuntimeError(f"Key does not allow {duration} seconds")
    return {
        "checked_at": now(), "provider": PROVIDER, "model": model_id,
        "duration": duration, "size": size, "price": "unknown",
        "key_enabled": True, "active_concurrency": key_info.get("active_concurrency"),
        "concurrency_limit": key_info.get("concurrency_limit"),
        "model_durations": durations, "model_sizes": sizes,
    }


def read_references(paths: list[str]) -> list[dict]:
    if not 1 <= len(paths) <= 9:
        raise RuntimeError("Submit requires 1-9 reference images in mount order")
    references = []
    for position, source in enumerate(paths, 1):
        path = Path(source).resolve()
        if not path.is_file():
            raise RuntimeError(f"Reference {position} is not a file: {path}")
        if not 0 < path.stat().st_size <= 15 * 1024 * 1024:
            raise RuntimeError(f"Reference {position} must be 1 byte to 15 MB")
        content = path.read_bytes()
        if content.startswith(b"\x89PNG\r\n\x1a\n") and path.suffix.lower() == ".png":
            mime = "image/png"
        elif content.startswith(b"\xff\xd8") and path.suffix.lower() in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif content[:4] == b"RIFF" and content[8:12] == b"WEBP" and path.suffix.lower() == ".webp":
            mime = "image/webp"
        else:
            raise RuntimeError(f"Reference {position} must be a matching PNG, JPEG or WEBP file")
        references.append({"position": position, "path": str(path), "content": content,
                           "mime": mime, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
    return references


def multipart_body(fields: dict, references: list[dict]) -> tuple[bytes, str]:
    boundary = "dolasd-" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                      str(value).encode("utf-8"), b"\r\n"))
    for ref in references:
        suffix = Path(ref["path"]).suffix.lower()
        filename = f'reference-{ref["position"]:02d}{suffix}'
        parts.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                      f'Content-Type: {ref["mime"]}\r\n\r\n'.encode("ascii"), ref["content"], b"\r\n"))
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def submit(client: Client, args: argparse.Namespace) -> None:
    if args.ledger.exists():
        raise RuntimeError("This ledger already has a creation attempt; reconcile it before any new POST")
    if not args.prompt_file:
        raise RuntimeError("--prompt-file is required for submit")
    prompt = Path(args.prompt_file).read_text(encoding="utf-8-sig").strip()
    if not prompt:
        raise RuntimeError("Prompt file is empty")
    references = read_references(args.reference or [])
    report = getattr(args, "preflight_report", None)
    if report is None:
        report = live_check(client, args.model, args.duration, args.size)
    elif any(report.get(key) != value for key, value in (("provider", PROVIDER), ("model", args.model),
                                                         ("duration", args.duration), ("size", args.size))):
        raise RuntimeError("Batch preflight does not match this request")
    report["reference_count"] = len(references)
    save(args.task_dir / "preflight.json", report)
    fields = {"model": args.model, "prompt": prompt, "duration": args.duration, "size": args.size}
    metadata = [{key: value for key, value in ref.items() if key != "content"} for ref in references]
    save(args.task_dir / "request.json", {**fields, "references": metadata})
    body, content_type = multipart_body(fields, references)
    base = {"unit": getattr(args, "unit", None), "model": args.model, "duration": args.duration, "size": args.size,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "price": "unknown",
            "reference_sha256s": [ref["sha256"] for ref in references]}
    append(args.ledger, {**base, "status": "submitting"})
    try:
        status, data = client.call("submit", blob=body, content_type=content_type, timeout=600)
    except Exception as exc:
        append(args.ledger, {"status": "unknown_outcome", "error": client.clean(str(exc))[:1000]})
        raise RuntimeError("Creation outcome unknown; reconcile the original request before retrying") from None
    save(args.task_dir / "create-response.json", data)
    if status not in (200, 201, 202):
        state = "submit_failed" if 400 <= status < 500 else "unknown_outcome"
        append(args.ledger, {"status": state, "http_status": status, "error": client.clean(data)[:1000]})
        raise RuntimeError(f"Creation HTTP {status}: {client.clean(data)[:500]}")
    task = data.get("data", data) if isinstance(data, dict) else {}
    task_id = task.get("id") or task.get("task_id") if isinstance(task, dict) else None
    if not task_id:
        append(args.ledger, {"status": "unknown_outcome", "error": "Successful response lacked task ID"})
        raise RuntimeError("Creation returned no task ID; do not retry automatically")
    append(args.ledger, {"task_id": str(task_id), "status": task.get("status", "queued")})
    print(json.dumps({"task_id": task_id, "status": task.get("status", "queued"), "price": "unknown"}, ensure_ascii=False))


def download(client: Client, task_id: str, target: Path) -> None:
    _, url = client.url("download", task_id)
    origin = parse.urlsplit(client.base).netloc
    for _ in range(5):
        parsed = parse.urlsplit(url)
        if parsed.scheme != "https":
            raise RuntimeError("Download URL is not HTTPS")
        headers = {"User-Agent": "ShortDramaDolasd/1.0"}
        if parsed.netloc == origin:
            headers["Authorization"] = "Bearer " + client.token
        try:
            response = client.opener.open(request.Request(url, headers=headers), timeout=180)
        except error.HTTPError as exc:
            response = exc
        with response:
            if response.code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError("Download redirect lacked Location")
                url = parse.urljoin(url, location)
                continue
            if response.code != 200:
                raise RuntimeError(f"Download HTTP {response.code}")
            if "json" in response.headers.get("Content-Type", ""):
                data = json.load(response)
                data = data.get("data", data) if isinstance(data, dict) else {}
                url = data.get("url") or data.get("video_url")
                if not url:
                    raise RuntimeError("Download response lacked video URL")
                continue
            with target.open("xb") as handle:
                shutil.copyfileobj(response, handle, 1024 * 1024)
            return
    raise RuntimeError("Too many download redirects")


def binary(name: str) -> str:
    local = ROOT / (name + ".exe")
    found = str(local) if local.is_file() else shutil.which(name)
    if not found:
        raise RuntimeError(f"Missing {name}")
    return found


def probe(path: Path) -> dict:
    run = subprocess.run([binary("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if run.returncode:
        raise RuntimeError("ffprobe failed")
    return json.loads(run.stdout)


def verify(path: Path, duration: int, size: str) -> dict:
    info = probe(path)
    video = next((stream for stream in info.get("streams", []) if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in info.get("streams", []) if stream.get("codec_type") == "audio"), None)
    if not video:
        raise RuntimeError("Downloaded file has no video stream")
    if video.get("codec_name") != "h264" or not audio or audio.get("codec_name") != "aac":
        raise RuntimeError("Final file is not H.264/AAC")
    width, height = map(int, size.split("x"))
    actual_duration = float(info["format"]["duration"])
    if (video.get("width"), video.get("height")) != (width, height) or abs(actual_duration - duration) > 1:
        raise RuntimeError("Downloaded size or duration differs from request")
    run = subprocess.run([binary("ffmpeg"), "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode", "-i", str(path), "-f", "null", "-"],
                         capture_output=True, timeout=max(180, duration * 10), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if run.returncode:
        raise RuntimeError("Full video decode failed")
    return {"width": width, "height": height, "duration": actual_duration,
            "video_codec": video.get("codec_name"),
            "audio_codec": audio.get("codec_name"),
            "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "full_decode": True}


def poll(client: Client, args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    while True:
        state = ledger_state(args.ledger)
        task_id = state.get("task_id")
        if not task_id:
            raise RuntimeError("No task ID in ledger; reconcile the creation outcome")
        if state.get("status") == "succeeded":
            print(json.dumps({"task_id": task_id, "status": "succeeded", "output_path": state.get("output_path")}, ensure_ascii=False))
            return 0
        status, data = client.call("detail", task_id=task_id)
        if status != 200:
            raise RuntimeError(f"Status HTTP {status}: {client.clean(data)[:500]}")
        save(args.task_dir / "last-response.json", data)
        task = data.get("data", data) if isinstance(data, dict) else {}
        remote = str(task.get("status") or "unknown").lower()
        if remote != state.get("status"):
            append(args.ledger, {"status": remote})
        print(json.dumps({"task_id": task_id, "remote_status": remote}, ensure_ascii=False))
        if remote in ("completed", "succeeded", "success"):
            args.output_dir.mkdir(parents=True, exist_ok=True)
            target = args.output_dir / f"dolasd-{task_id}.mp4"
            if target.exists():
                raise RuntimeError(f"Output already exists: {target}")
            temp = args.output_dir / f"dolasd-{task_id}.{uuid.uuid4().hex}.part"
            download(client, task_id, temp)
            raw = probe(temp)
            video = next((stream for stream in raw.get("streams", []) if stream.get("codec_type") == "video"), {})
            audio = next((stream for stream in raw.get("streams", []) if stream.get("codec_type") == "audio"), {})
            if video.get("codec_name") != "h264" or audio.get("codec_name") != "aac":
                original = args.output_dir / f"dolasd-{task_id}-original.mp4"
                if original.exists():
                    raise RuntimeError(f"Original output already exists: {original}")
                temp.rename(original)
                if not audio:
                    raise RuntimeError("Original video has no audio stream; retained for diagnosis")
                temp = args.output_dir / f"dolasd-{task_id}.{uuid.uuid4().hex}.converted.mp4"
                run = subprocess.run([binary("ffmpeg"), "-nostdin", "-v", "error", "-n", "-i", str(original),
                                      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(temp)],
                                     capture_output=True, timeout=max(300, int(state["duration"]) * 15),
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if run.returncode:
                    raise RuntimeError("Transcode failed; original video retained")
            report = verify(temp, int(state["duration"]), state["size"])
            temp.rename(target)
            report.update({"task_id": task_id, "status": "succeeded", "output_path": str(target)})
            save(args.task_dir / "technical-report.json", report)
            append(args.ledger, report)
            print(json.dumps(report, ensure_ascii=False))
            return 0
        if remote in ("failed", "error", "canceled", "cancelled"):
            message = client.clean(task.get("error") or task.get("failure_kind") or remote)
            append(args.ledger, {"status": remote, "error": message[:1000]})
            raise RuntimeError(f"Remote task ended: {message[:500]}")
        if not args.watch or time.monotonic() >= deadline:
            return 3
        time.sleep(args.interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dolasd video channel: check, one-shot submit, poll and verify")
    parser.add_argument("mode", choices=("check", "submit", "poll"))
    parser.add_argument("--prompt-file")
    parser.add_argument("--unit", type=int, help="Storyboard unit number for the task ledger")
    parser.add_argument("--reference", action="append", help="Local PNG/JPEG/WEBP reference; repeat in mount order (1-9)")
    parser.add_argument("--model")
    parser.add_argument("--duration", type=int)
    parser.add_argument("--size")
    parser.add_argument("--task-dir", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    provider = read_config()["providers"][PROVIDER]
    defaults = provider["defaults"]
    args.model = args.model or provider["default_model"]
    args.duration = args.duration or defaults["duration"]
    args.size = args.size or defaults["size"]
    if args.interval < 1 or args.timeout < 0:
        parser.error("--interval must be positive and --timeout nonnegative")
    if args.mode != "check" and not args.task_dir:
        parser.error("--task-dir is required for submit and poll")
    if args.task_dir:
        args.task_dir = args.task_dir.resolve()
        args.ledger = args.ledger.resolve() if args.ledger else args.task_dir / "dolasd-tasks.jsonl"
        args.output_dir = args.output_dir.resolve() if args.output_dir else args.task_dir
    client = None
    try:
        client = Client()
        if args.mode == "check":
            print(json.dumps(live_check(client, args.model, args.duration, args.size), ensure_ascii=False))
            return 0
        if args.mode == "submit":
            args.task_dir.mkdir(parents=True, exist_ok=True)
            submit(client, args)
            return 0
        return poll(client, args)
    except Exception as exc:
        message = client.clean(str(exc)) if client else str(exc)
        print("ERROR " + message, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
