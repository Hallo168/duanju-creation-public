from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import uvicorn

from app.config import Settings
from app.integrity import compiled_bundle_root, verify_manifest
from app.licensing import LicenseError, LicenseManager, machine_code


def _manager() -> LicenseManager:
    settings = Settings.from_env()
    return LicenseManager.from_public_key_file(Path(settings.license_public_key_path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="SymphonyLocal")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("machine", help="显示本机机器码")
    sub.add_parser("status", help="显示许可证状态")
    activate = sub.add_parser("activate", help="从许可证文件激活")
    activate.add_argument("--license-file", type=Path, required=True)
    serve = sub.add_parser("serve", help="启动本地生成服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        root = compiled_bundle_root()
        if root is not None:
            verify_manifest(root, _manager().public_key)
        if args.command == "machine":
            print(machine_code())
        elif args.command == "status":
            print(json.dumps(_manager().status(), ensure_ascii=False, indent=2))
        elif args.command == "activate":
            token = args.license_file.read_text(encoding="utf-8").strip()
            verified = _manager().activate(token)
            print(json.dumps(verified.public_status(), ensure_ascii=False, indent=2))
        elif args.command == "serve":
            # Uvicorn imports the already license-gated FastAPI app. Keeping the
            # launcher in the compiled binary avoids distributing Python source.
            uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)
        return 0
    except (LicenseError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
