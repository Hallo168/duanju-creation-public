import os
from dataclasses import dataclass
from pathlib import Path


def _product_root() -> Path | None:
    value = os.getenv("SYMPHONY_PRODUCT_ROOT")
    return Path(value) if value else None


def _default_cli_path() -> str:
    root = _product_root()
    if root is not None:
        return str(root / "runtime" / "opencli" / "dist" / "src" / "main.js")
    repo_root = Path(__file__).resolve().parents[3]
    return str(repo_root / "外部依赖" / "OpenCLI-Symphony" / "dist" / "src" / "main.js")


def _default_asset_cache_path() -> str:
    root = _product_root()
    local_app_data = os.getenv("LOCALAPPDATA")
    if root is not None and local_app_data:
        return str(Path(local_app_data) / "Symphony" / "asset_cache.json")
    return str(Path(__file__).resolve().parents[1] / "data" / "asset_cache.json")

@dataclass(frozen=True)
class Settings:
    node_path: str
    cli_path: str
    asset_cache_path: str
    license_public_key_path: str
    direct_only: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            node_path=os.getenv("OPENCLI_NODE_PATH", "node"),
            cli_path=os.getenv("OPENCLI_CLI_PATH", _default_cli_path()),
            asset_cache_path=os.getenv("SYMPHONY_ASSET_CACHE", _default_asset_cache_path()),
            license_public_key_path=os.getenv(
                "SYMPHONY_LICENSE_PUBLIC_KEY",
                os.path.join(os.path.dirname(__file__), "license_public_key.pem"),
            ),
            direct_only=os.getenv("SYMPHONY_DIRECT_ONLY", "").strip().lower()
            in {"1", "true", "yes", "on"},
        )
