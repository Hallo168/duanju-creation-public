"""Flow Kit adapter for the fixed, single-reference colored-pencil route."""
from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
import shutil
import struct
import subprocess
import urllib.error
import urllib.request
import uuid

PENCIL_PROMPT = "不改变图片，把图片风格转换成素描彩铅画"
MODEL = "NARWHAL"


class FlowKitImageClient:
    def __init__(self, base_url, *, model=MODEL, aspect_ratio="9:16",
                 image_size="1K", prompt=PENCIL_PROMPT):
        if (model, aspect_ratio, image_size, prompt) != (MODEL, "9:16", "1K", PENCIL_PROMPT):
            raise ValueError("Flow Kit colored-pencil parameters differ from the fixed contract")
        self.base_url = base_url.rstrip("/")
        self.project_id = None
        self.parameters = dict(model=model, aspect_ratio=aspect_ratio,
                               image_size=image_size, prompt=prompt)
        self._api = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, path, payload=None, *, raw=None, content_type=None, timeout=30):
        data = raw if raw is not None else (
            json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None)
        headers = {"X-FlowKit-Caller": "short-drama-colored-pencil"}
        if data is not None:
            headers["Content-Type"] = content_type or "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers)
        try:
            with self._api.open(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1000]
            raise RuntimeError(f"Flow Kit HTTP {exc.code}: {detail}") from exc
        if isinstance(result, dict) and (result.get("error") or result.get("detail")):
            raise RuntimeError("Flow Kit: " + str(result.get("error") or result.get("detail")))
        return result

    def preflight(self, expected_project_id=None):
        health = self._request("/health")
        status = self._request("/api/flow/status")
        if health.get("status") != "ok" or not health.get("extension_connected") or not status.get("connected"):
            raise RuntimeError("Flow Kit extension is not connected")
        if status.get("transport") != "batch":
            raise RuntimeError("Flow Kit batch transport is required")
        project_id = status.get("flow_project_id")
        try:
            uuid.UUID(project_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError("Flow Kit has no valid pinned project") from exc
        if expected_project_id and expected_project_id != project_id:
            raise RuntimeError("FLOW working project changed; stopped before image submission")
        if status.get("generation_throttle", {}).get("cooldown_active"):
            raise RuntimeError("Flow Kit generation cooldown is active; wait before submitting")
        self.project_id = project_id
        return {"health": health, "status": status, "parameters": self.parameters}

    @staticmethod
    def _save(path, value):
        path = Path(path)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _upload(self, source):
        data = source.read_bytes()
        mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else (
            "image/jpeg" if data.startswith(b"\xff\xd8\xff") else mimetypes.guess_type(source.name)[0])
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("Unsupported reference image format")
        boundary = "flowkit-" + uuid.uuid4().hex
        # ASCII multipart filename; the actual source path and hash stay in the ledger.
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="project_id"\r\n\r\n'
                f'{self.project_id}\r\n--{boundary}\r\n'
                f'Content-Disposition: form-data; name="file"; filename="reference{source.suffix.lower()}"\r\n'
                f'Content-Type: {mime}\r\n\r\n').encode("utf-8")
        body += data + f"\r\n--{boundary}--\r\n".encode("ascii")
        return self._request("/api/flow/upload-image-file", raw=body,
                             content_type="multipart/form-data; boundary=" + boundary, timeout=180)

    @staticmethod
    def _media(result):
        media = result.get("media") or []
        if result.get("complete") is False or len(media) != 1:
            raise RuntimeError("Flow Kit did not return exactly one completed image")
        item = media[0]
        media_id = item.get("name") or item.get("mediaId") or item.get("media_id")
        image = item.get("image") or {}
        url = image.get("generatedImage", {}).get("fifeUrl") or image.get("fifeUrl") or item.get("fifeUrl") or item.get("url")
        try:
            uuid.UUID(media_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError("Flow Kit returned an invalid media id") from exc
        if not url or not url.startswith("https://"):
            raise RuntimeError("Flow Kit returned no HTTPS image URL")
        return media_id, url

    @staticmethod
    def _download(url):
        with urllib.request.urlopen(url, timeout=90) as response:
            return response.read()

    @staticmethod
    def _png(data, evidence):
        source, output = evidence / "downloaded-image.bin", evidence / "result.png"
        source.write_bytes(data)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required to save the PNG asset")
        conversion = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(source), "-frames:v", "1", str(output)],
            capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if conversion.returncode or not output.exists():
            raise RuntimeError("Flow Kit image-to-PNG conversion failed")
        png = output.read_bytes()
        if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) < 1024:
            raise RuntimeError("Flow Kit result is not a valid PNG asset")
        width, height = struct.unpack(">II", png[16:24])
        if width <= 0 or height <= 0 or abs(width / height - 9 / 16) > 0.02:
            raise RuntimeError(f"Flow Kit returned unexpected portrait dimensions: {width}x{height}")
        return png, width, height

    def generate(self, source_path, operation_id, evidence_dir):
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if not self.project_id:
            self.preflight()
        evidence = Path(evidence_dir)
        evidence.mkdir(parents=True, exist_ok=True)
        ledger_path = evidence / "operation.json"
        identity = {"operation_id": operation_id, "source_path": str(source),
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "project_id": self.project_id, **self.parameters}
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {**identity, "state": "prepared"}
        if any(ledger.get(key) != value for key, value in identity.items()):
            raise RuntimeError("Flow Kit operation identity changed; reconcile existing evidence first")
        self._save(ledger_path, ledger)
        if ledger["state"] in {"upload_submitting", "generate_submitting", "outcome_unknown", "failed"}:
            raise RuntimeError("Existing Flow Kit attempt requires reconciliation; not resubmitting: " + str(ledger.get("error", ledger["state"])))
        cached = evidence / "result.png"
        if ledger["state"] == "completed" and cached.exists():
            png = cached.read_bytes()
            if hashlib.sha256(png).hexdigest() == ledger.get("output_sha256"):
                return png
        # Re-check the actual working project before any upload/edit submission.
        self.preflight(identity["project_id"])
        if ledger["state"] == "prepared":
            ledger["state"] = "upload_submitting"
            self._save(ledger_path, ledger)
            try:
                uploaded = self._upload(source)
                self._save(evidence / "upload-response.json", uploaded)
                source_media_id = uploaded.get("media_id")
                uuid.UUID(source_media_id)
                if uploaded.get("project_id") != self.project_id:
                    raise RuntimeError("Uploaded reference belongs to a different project")
            except Exception as exc:
                ledger.update(state="outcome_unknown", error=str(exc))
                self._save(ledger_path, ledger)
                raise
            ledger.update(state="uploaded", source_media_id=source_media_id)
            self._save(ledger_path, ledger)
        if ledger["state"] == "uploaded":
            payload = {"prompt": PENCIL_PROMPT, "source_media_id": ledger["source_media_id"],
                       "project_id": self.project_id, "image_model": MODEL,
                       "aspect_ratio": "9:16", "count": 1,
                       "seed": int(hashlib.sha256(operation_id.encode()).hexdigest()[:8], 16) % 1_000_000_000 + 1}
            self._save(evidence / "edit-request.json", payload)
            ledger["state"] = "generate_submitting"
            self._save(ledger_path, ledger)
            try:
                generated = self._request("/api/flow/edit-image", payload, timeout=420)
                self._save(evidence / "edit-response.json", generated)
                media_id, url = self._media(generated)
            except Exception as exc:
                ledger.update(state="outcome_unknown", error=str(exc))
                self._save(ledger_path, ledger)
                raise
            ledger.update(state="generated", media_id=media_id, image_url=url)
            self._save(ledger_path, ledger)
        if ledger["state"] in {"generated", "completed"}:
            # A download/PNG failure retains the media receipt; it never calls the model again.
            try:
                data = self._download(ledger["image_url"])
            except urllib.error.HTTPError as exc:
                exc.close()
                if exc.code not in (401, 403, 404):
                    raise
                fresh = self._request("/api/flow/media/" + ledger["media_id"])
                fresh_url = (fresh.get("image") or {}).get("fifeUrl")
                if not fresh_url or not fresh_url.startswith("https://"):
                    raise RuntimeError("Flow Kit could not refresh the image URL")
                ledger["image_url"] = fresh_url
                self._save(ledger_path, ledger)
                data = self._download(ledger["image_url"])
            png, width, height = self._png(data, evidence)
            ledger.update(state="completed", width=width, height=height,
                          output_sha256=hashlib.sha256(png).hexdigest())
            self._save(ledger_path, ledger)
            return png
        raise RuntimeError("Unrecognized Flow Kit operation state")
