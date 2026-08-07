from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .licensing import LicenseError, _b64url_decode, canonical_json


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_manifest(root: Path, public_key: Ed25519PublicKey) -> dict[str, Any]:
    manifest_path = root / "integrity.json"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload = document["payload"]
        signature = _b64url_decode(document["signature"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise LicenseError("integrity_manifest_invalid", "安装包完整性清单缺失或损坏") from exc
    try:
        public_key.verify(signature, canonical_json(payload))
    except InvalidSignature as exc:
        raise LicenseError("integrity_signature_invalid", "安装包完整性签名无效") from exc
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise LicenseError("integrity_manifest_invalid", "安装包完整性清单为空")
    for relative, expected in files.items():
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise LicenseError("integrity_manifest_invalid", "完整性清单包含越界路径") from exc
        if not candidate.is_file():
            raise LicenseError("integrity_file_missing", f"安装文件缺失：{relative}")
        if candidate.stat().st_size != int(expected.get("size", -1)):
            raise LicenseError("integrity_size_mismatch", f"安装文件大小异常：{relative}")
        if _file_digest(candidate) != expected.get("sha256"):
            raise LicenseError("integrity_hash_mismatch", f"安装文件已被修改：{relative}")
    return payload


def compiled_bundle_root() -> Path | None:
    # Nuitka defines __compiled__ in compiled modules and freezes sys.executable
    # inside the standalone directory. Prefer locating the signed manifest so
    # the install directory may move without changing the runtime layout.
    if "__compiled__" not in globals() and not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if (parent / "integrity.json").is_file():
            return parent
    raise LicenseError(
        "integrity_manifest_missing",
        "安装包完整性清单缺失，拒绝启动",
    )
