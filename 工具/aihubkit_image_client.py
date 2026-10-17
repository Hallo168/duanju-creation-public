"""AIHubKit Gemini adapter for single-reference colored-pencil derivatives."""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from pathlib import Path
import shutil
import struct
import subprocess
import urllib.error
import urllib.parse
import urllib.request

MODEL = "gemini-3.1-flash-image-preview"
PENCIL_PROMPT = "不改变图片，把图片风格转换成素描彩铅画"


class AIHubKitImageClient:
    def __init__(self, base_url, *, api_key, model=MODEL, aspect_ratio="9:16",
                 image_size="1K", prompt=PENCIL_PROMPT):
        if (model, aspect_ratio, image_size, prompt) != (MODEL, "9:16", "1K", PENCIL_PROMPT):
            raise ValueError("AIHubKit colored-pencil parameters differ from the fixed contract")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.parameters = dict(model=model, aspect_ratio=aspect_ratio,
                               image_size=image_size, prompt=prompt)

    def _request(self, path, payload=None, timeout=30):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base_url + path, data=body, headers={
            "Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").replace(self.api_key, "[REDACTED]")[:1000]
            raise RuntimeError(f"AIHubKit HTTP {exc.code}: {detail}") from exc

    def preflight(self):
        models = self._request("/v1/models")
        if not any(item.get("id") == self.parameters["model"] for item in models.get("data", [])):
            raise RuntimeError("Selected AIHubKit model is unavailable; stopped before submission")
        return self.parameters

    @staticmethod
    def _save(path, value):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _payload(self, source):
        data = source.read_bytes()
        mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else (
            "image/jpeg" if data.startswith(b"\xff\xd8\xff") else mimetypes.guess_type(source.name)[0])
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("Unsupported reference image format")
        return {
            "contents": [{"role": "user", "parts": [
                {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}},
                {"text": self.parameters["prompt"]}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": {"aspectRatio": "9:16", "imageSize": "1K"}}}

    @staticmethod
    def _image_bytes(response):
        images = []
        for candidate in response.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                item = part.get("inlineData") or part.get("inline_data")
                if item and str(item.get("mimeType") or item.get("mime_type", "")).startswith("image/"):
                    images.append(item["data"])
        if len(images) != 1:
            raise RuntimeError("AIHubKit did not return exactly one image; inspect the saved response")
        return base64.b64decode(images[0], validate=True)

    @staticmethod
    def _png(data, evidence):
        source, output = evidence / "downloaded-image.bin", evidence / "result.png"
        source.write_bytes(data)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required to save the PNG asset")
        result = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(source),
                                 "-frames:v", "1", str(output)], capture_output=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or not output.exists():
            raise RuntimeError("AIHubKit image-to-PNG conversion failed")
        png = output.read_bytes()
        if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) < 1024:
            raise RuntimeError("AIHubKit result is not a valid PNG asset")
        width, height = struct.unpack(">II", png[16:24])
        if width <= 0 or height <= 0 or abs(width / height - 9 / 16) > 0.02:
            raise RuntimeError(f"AIHubKit returned unexpected portrait dimensions: {width}x{height}")
        return png, width, height

    def generate(self, source_path, operation_id, evidence_dir):
        source = Path(source_path).resolve()
        evidence = Path(evidence_dir)
        evidence.mkdir(parents=True, exist_ok=True)
        ledger_path = evidence / "operation.json"
        identity = {"operation_id": operation_id, "source_path": str(source),
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "base_url": self.base_url, **self.parameters}
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {**identity, "state": "prepared"}
        if any(ledger.get(key) != value for key, value in identity.items()):
            raise RuntimeError("AIHubKit operation identity changed; reconcile existing evidence first")
        self._save(ledger_path, ledger)
        response_path = evidence / "generate-response.json"
        # A saved response is recoverable even if interrupted before the ledger update.
        if ledger["state"] in {"generate_submitting", "outcome_unknown"} and response_path.exists():
            ledger["state"] = "generated"
            self._save(ledger_path, ledger)
        if ledger["state"] in {"generate_submitting", "outcome_unknown"}:
            raise RuntimeError("Existing AIHubKit submission requires reconciliation; not resubmitting")
        cached = evidence / "result.png"
        if ledger["state"] == "completed" and cached.exists():
            png = cached.read_bytes()
            if hashlib.sha256(png).hexdigest() == ledger.get("output_sha256"):
                return png
        if ledger["state"] == "prepared":
            payload = self._payload(source)
            self._save(evidence / "generate-request.json", payload)
            ledger["state"] = "generate_submitting"
            self._save(ledger_path, ledger)
            try:
                path = "/v1beta/models/" + urllib.parse.quote(self.parameters["model"], safe="") + ":generateContent"
                response = self._request(path, payload, timeout=600)
                self._save(response_path, response)
            except Exception as exc:
                ledger.update(state="outcome_unknown", error=str(exc).replace(self.api_key, "[REDACTED]"))
                self._save(ledger_path, ledger)
                raise
            ledger["state"] = "generated"
            self._save(ledger_path, ledger)
        if ledger["state"] in {"generated", "completed"}:
            response = json.loads(response_path.read_text(encoding="utf-8"))
            png, width, height = self._png(self._image_bytes(response), evidence)
            ledger.update(state="completed", width=width, height=height,
                          output_sha256=hashlib.sha256(png).hexdigest())
            self._save(ledger_path, ledger)
            return png
        raise RuntimeError("Unrecognized AIHubKit operation state")
