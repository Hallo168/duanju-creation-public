from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import keyring
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PRODUCT_ID = "tiktok-symphony-proxy"
PRODUCT_VERSION = "0.1.0"
LICENSE_PREFIX = "TSP1"
KEYRING_SERVICE = "tiktok-symphony-proxy"
LICENSE_ACCOUNT = "offline-license-v1"
CLOCK_ACCOUNT = "offline-license-clock-v1"
MACHINE_NAMESPACE = b"tiktok-symphony-proxy/machine/v1\0"


class LicenseError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise LicenseError("license_malformed", "许可证编码损坏") from exc


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise LicenseError("license_malformed", f"许可证缺少 {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LicenseError("license_malformed", f"许可证 {field} 格式错误") from exc
    if parsed.tzinfo is None:
        raise LicenseError("license_malformed", f"许可证 {field} 必须包含时区")
    return parsed.astimezone(timezone.utc)


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except (AttributeError, ValueError) as exc:
        raise LicenseError("license_malformed", "许可证版本范围无效") from exc


def _machine_guid() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        flags = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            flags,
        ) as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0]).strip()
    except OSError:
        return ""


def _system_volume_serial() -> str:
    if sys.platform != "win32":
        return ""
    root = Path(os.environ.get("SystemRoot", r"C:\Windows")).anchor or "C:\\"
    serial = ctypes.c_uint32()
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root),
        None,
        0,
        ctypes.byref(serial),
        None,
        None,
        None,
        0,
    )
    return f"{serial.value:08X}" if ok else ""


def _smbios_uuid() -> str:
    if sys.platform != "win32":
        return ""
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
    ]
    try:
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
            check=False,
            startupinfo=startup,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


@lru_cache(maxsize=1)
def machine_digest() -> str:
    components = [
        value.lower()
        for value in (_machine_guid(), _system_volume_serial(), _smbios_uuid())
        if value and value.strip("0-").lower() not in {"none", "unknown"}
    ]
    if len(components) < 2:
        raise LicenseError("machine_unavailable", "无法读取足够的 Windows 设备标识")
    return hashlib.sha256(MACHINE_NAMESPACE + "\0".join(components).encode("utf-8")).hexdigest()


def machine_code(digest: str | None = None) -> str:
    raw = bytes.fromhex(digest or machine_digest())
    encoded = base64.b32encode(raw).decode("ascii").rstrip("=")
    groups = "-".join(encoded[index:index + 5] for index in range(0, len(encoded), 5))
    checksum = hashlib.sha256(raw + b"/display").hexdigest()[:4].upper()
    return f"TSP1-{groups}-{checksum}"


def parse_machine_code(value: str) -> str:
    parts = value.strip().upper().split("-")
    if len(parts) < 4 or parts[0] != "TSP1":
        raise LicenseError("machine_code_invalid", "机器码格式错误")
    checksum = parts[-1]
    encoded = "".join(parts[1:-1])
    try:
        raw = base64.b32decode(encoded + "=" * (-len(encoded) % 8))
    except (ValueError, TypeError) as exc:
        raise LicenseError("machine_code_invalid", "机器码编码损坏") from exc
    if len(raw) != 32:
        raise LicenseError("machine_code_invalid", "机器码长度错误")
    expected = hashlib.sha256(raw + b"/display").hexdigest()[:4].upper()
    if checksum != expected:
        raise LicenseError("machine_code_invalid", "机器码校验失败")
    return raw.hex()


@dataclass(frozen=True)
class VerifiedLicense:
    payload: dict[str, Any]

    def public_status(self) -> dict[str, Any]:
        return {
            "valid": True,
            "licenseId": self.payload["license_id"],
            "edition": self.payload["edition"],
            "expiresAt": self.payload.get("expires_at"),
            "features": self.payload["features"],
            "machineCode": machine_code(self.payload["machine"]),
            "productVersion": PRODUCT_VERSION,
        }


class LicenseManager:
    def __init__(
        self,
        public_key: Ed25519PublicKey,
        *,
        store: Any = keyring,
        clock: Callable[[], datetime] | None = None,
        machine_hash: str | None = None,
    ) -> None:
        self.public_key = public_key
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.machine_hash = machine_hash or machine_digest()

    @classmethod
    def from_public_key_file(cls, path: Path) -> "LicenseManager":
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise LicenseError(
                "license_not_configured",
                "软件发行公钥尚未配置，请联系发行者",
            ) from exc
        try:
            key = serialization.load_pem_public_key(raw)
        except (ValueError, TypeError) as exc:
            raise LicenseError("license_not_configured", "软件发行公钥无效") from exc
        if not isinstance(key, Ed25519PublicKey):
            raise LicenseError("license_not_configured", "软件发行公钥类型错误")
        return cls(key)

    def installed_token(self) -> str | None:
        return self.store.get_password(KEYRING_SERVICE, LICENSE_ACCOUNT)

    def activate(self, token: str) -> VerifiedLicense:
        verified = self.verify(token)
        self.store.set_password(KEYRING_SERVICE, LICENSE_ACCOUNT, token.strip())
        self._write_clock(self.clock())
        return verified

    def verify_installed(self, feature: str | None = None) -> VerifiedLicense:
        token = self.installed_token()
        if not token:
            raise LicenseError("license_required", "软件尚未激活")
        return self.verify(token, feature=feature)

    def verify(self, token: str, feature: str | None = None) -> VerifiedLicense:
        parts = token.strip().split(".")
        if len(parts) != 3 or parts[0] != LICENSE_PREFIX:
            raise LicenseError("license_malformed", "许可证格式错误")
        payload_bytes = _b64url_decode(parts[1])
        signature = _b64url_decode(parts[2])
        try:
            self.public_key.verify(signature, payload_bytes)
        except InvalidSignature as exc:
            raise LicenseError("license_signature_invalid", "许可证签名无效") from exc
        try:
            payload = json.loads(payload_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LicenseError("license_malformed", "许可证内容损坏") from exc
        if not isinstance(payload, dict) or canonical_json(payload) != payload_bytes:
            raise LicenseError("license_malformed", "许可证不是规范编码")
        self._validate_payload(payload, feature)
        return VerifiedLicense(payload)

    def status(self) -> dict[str, Any]:
        try:
            return self.verify_installed().public_status()
        except LicenseError as exc:
            return {
                "valid": False,
                "code": exc.code,
                "message": str(exc),
                "machineCode": machine_code(self.machine_hash),
                "productVersion": PRODUCT_VERSION,
            }

    def _validate_payload(self, payload: dict[str, Any], feature: str | None) -> None:
        required = {
            "version", "license_id", "product", "machine", "edition",
            "issued_at", "not_before", "features",
        }
        if not required.issubset(payload):
            raise LicenseError("license_malformed", "许可证字段不完整")
        if payload["version"] != 1 or payload["product"] != PRODUCT_ID:
            raise LicenseError("license_wrong_product", "许可证不属于本软件")
        if payload["machine"] != self.machine_hash:
            raise LicenseError("license_wrong_machine", "许可证与本机机器码不匹配")
        features = payload["features"]
        if not isinstance(features, list) or not all(isinstance(item, str) for item in features):
            raise LicenseError("license_malformed", "许可证功能列表无效")
        if feature and "*" not in features and feature not in features:
            raise LicenseError("license_feature_denied", f"当前许可证不包含功能：{feature}")
        now = self.clock().astimezone(timezone.utc)
        issued_at = _parse_utc(payload["issued_at"], "issued_at")
        not_before = _parse_utc(payload["not_before"], "not_before")
        if issued_at > now.replace(microsecond=0):
            raise LicenseError("license_not_yet_valid", "许可证签发时间晚于当前时间")
        if now < not_before:
            raise LicenseError("license_not_yet_valid", "许可证尚未生效")
        if payload.get("expires_at") is not None and now > _parse_utc(payload["expires_at"], "expires_at"):
            raise LicenseError("license_expired", "许可证已到期")
        if payload.get("max_version") and _version_tuple(PRODUCT_VERSION) > _version_tuple(payload["max_version"]):
            raise LicenseError("license_version_denied", "许可证不支持当前软件版本")
        last_seen = self._read_clock()
        if last_seen and now.timestamp() + 300 < last_seen:
            raise LicenseError("clock_rollback", "检测到系统时间回拨，请校准时间后重试")
        self._write_clock(now)

    def _read_clock(self) -> float | None:
        raw = self.store.get_password(KEYRING_SERVICE, CLOCK_ACCOUNT)
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    def _write_clock(self, now: datetime) -> None:
        previous = self._read_clock() or 0
        current = max(previous, now.timestamp())
        self.store.set_password(KEYRING_SERVICE, CLOCK_ACCOUNT, str(current))
