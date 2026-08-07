from __future__ import annotations

import sys
from pathlib import Path

from app.config import Settings
from app.integrity import compiled_bundle_root, verify_manifest
from app.licensing import LicenseError, LicenseManager


def _manager() -> LicenseManager:
    settings = Settings.from_env()
    return LicenseManager.from_public_key_file(Path(settings.license_public_key_path))


def main() -> int:
    try:
        root = compiled_bundle_root()
        if root is not None:
            verify_manifest(root, _manager().public_key)
        from symphony_video_batch import main as batch_main

        return batch_main()
    except (LicenseError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
