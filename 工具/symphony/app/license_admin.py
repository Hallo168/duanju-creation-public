from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import keyring
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .licensing import (
    LICENSE_PREFIX,
    PRODUCT_ID,
    PRODUCT_VERSION,
    _b64url_encode,
    canonical_json,
    parse_machine_code,
)

ISSUER_SERVICE = "tiktok-symphony-license-issuer"
ISSUER_ACCOUNT = "ed25519-private-v1"


def _fingerprint(public_pem: bytes) -> str:
    return hashlib.sha256(public_pem).hexdigest()[:16].upper()


def _load_private() -> Ed25519PrivateKey:
    encoded = keyring.get_password(ISSUER_SERVICE, ISSUER_ACCOUNT)
    if not encoded:
        raise RuntimeError("发行私钥尚未初始化，请先运行 init")
    raw = base64.b64decode(encoded)
    return Ed25519PrivateKey.from_private_bytes(raw)


def initialize(public_out: Path) -> dict[str, str]:
    if keyring.get_password(ISSUER_SERVICE, ISSUER_ACCOUNT):
        raise RuntimeError("发行私钥已经存在；为避免旧许可证失效，禁止覆盖")
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    keyring.set_password(ISSUER_SERVICE, ISSUER_ACCOUNT, base64.b64encode(raw).decode("ascii"))
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_out.parent.mkdir(parents=True, exist_ok=True)
    public_out.write_bytes(public_pem)
    return {"publicKey": str(public_out), "fingerprint": _fingerprint(public_pem)}


def issue(
    *,
    machine: str,
    edition: str,
    features: list[str],
    days: int | None,
    expires_at: str | None,
    max_version: str,
    customer: str | None,
) -> str:
    private = _load_private()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expiry = None
    if days is not None:
        if days < 1:
            raise ValueError("days 必须大于 0")
        expiry = now + timedelta(days=days)
    elif expires_at:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        if expiry <= now:
            raise ValueError("expires-at 必须晚于当前时间")
    payload = {
        "version": 1,
        "license_id": str(uuid4()),
        "product": PRODUCT_ID,
        "machine": parse_machine_code(machine),
        "edition": edition,
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "not_before": now.isoformat().replace("+00:00", "Z"),
        "expires_at": expiry.isoformat().replace("+00:00", "Z") if expiry else None,
        "max_version": max_version,
        "features": sorted(set(features)),
        "nonce": base64.urlsafe_b64encode(uuid4().bytes).decode("ascii").rstrip("="),
    }
    if customer:
        payload["customer"] = customer
    encoded = canonical_json(payload)
    signature = private.sign(encoded)
    return f"{LICENSE_PREFIX}.{_b64url_encode(encoded)}.{_b64url_encode(signature)}"


def export_backup(destination: Path) -> dict[str, str]:
    private = _load_private()
    first = getpass.getpass("输入备份密码（至少 16 个字符）: ")
    second = getpass.getpass("再次输入备份密码: ")
    if first != second:
        raise ValueError("两次密码不一致")
    if len(first) < 16:
        raise ValueError("备份密码至少 16 个字符")
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(first.encode("utf-8")),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(pem)
    return {"backup": str(destination), "bytes": str(len(pem))}


def import_backup(source: Path, public_out: Path) -> dict[str, str]:
    if keyring.get_password(ISSUER_SERVICE, ISSUER_ACCOUNT):
        raise RuntimeError("发行私钥已经存在；禁止覆盖")
    password = getpass.getpass("输入备份密码: ").encode("utf-8")
    key = serialization.load_pem_private_key(source.read_bytes(), password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("备份不是 Ed25519 私钥")
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    keyring.set_password(ISSUER_SERVICE, ISSUER_ACCOUNT, base64.b64encode(raw).decode("ascii"))
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_out.parent.mkdir(parents=True, exist_ok=True)
    public_out.write_bytes(public_pem)
    return {"publicKey": str(public_out), "fingerprint": _fingerprint(public_pem)}


def sign_bundle(root: Path) -> dict[str, str]:
    private = _load_private()
    resolved = root.resolve()
    files: dict[str, dict[str, int | str]] = {}
    for path in sorted(resolved.rglob("*")):
        if not path.is_file() or path.name == "integrity.json":
            continue
        relative = path.relative_to(resolved).as_posix()
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        files[relative] = {"sha256": hasher.hexdigest(), "size": path.stat().st_size}
    if not files:
        raise ValueError("待签名目录为空")
    payload = {
        "version": 1,
        "product": PRODUCT_ID,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "files": files,
    }
    signature = private.sign(canonical_json(payload))
    destination = resolved / "integrity.json"
    destination.write_text(
        json.dumps(
            {"payload": payload, "signature": _b64url_encode(signature)},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {"manifest": str(destination), "files": str(len(files))}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TikTok Symphony 离线许可证发行工具")
    sub = parser.add_subparsers(dest="command", required=True)
    default_public = Path(__file__).with_name("license_public_key.pem")

    init = sub.add_parser("init", help="生成发行密钥（只允许执行一次）")
    init.add_argument("--public-out", type=Path, default=default_public)

    create = sub.add_parser("issue", help="签发绑定机器的许可证")
    create.add_argument("--machine", required=True)
    create.add_argument("--edition", default="standard")
    create.add_argument("--features", default="generate,download,accounts")
    expiry = create.add_mutually_exclusive_group()
    expiry.add_argument("--days", type=int)
    expiry.add_argument("--expires-at")
    create.add_argument("--max-version", default=PRODUCT_VERSION)
    create.add_argument("--customer")
    create.add_argument("--out", type=Path)

    backup = sub.add_parser("backup", help="导出密码加密的发行私钥备份")
    backup.add_argument("--out", type=Path, required=True)

    restore = sub.add_parser("restore", help="从密码加密备份恢复发行私钥")
    restore.add_argument("--in", dest="source", type=Path, required=True)
    restore.add_argument("--public-out", type=Path, default=default_public)
    sign = sub.add_parser("sign-bundle", help="签名客户安装目录的完整性清单")
    sign.add_argument("--root", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "init":
            result = initialize(args.public_out)
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "issue":
            token = issue(
                machine=args.machine,
                edition=args.edition,
                features=[item.strip() for item in args.features.split(",") if item.strip()],
                days=args.days,
                expires_at=args.expires_at,
                max_version=args.max_version,
                customer=args.customer,
            )
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(token + "\n", encoding="utf-8")
                print(json.dumps({"license": str(args.out)}, ensure_ascii=False))
            else:
                print(token)
        elif args.command == "backup":
            print(json.dumps(export_backup(args.out), ensure_ascii=False))
        elif args.command == "restore":
            print(json.dumps(import_backup(args.source, args.public_out), ensure_ascii=False))
        elif args.command == "sign-bundle":
            print(json.dumps(sign_bundle(args.root), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
