from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from app import batch_product_cli, product_cli
from app.config import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = PROJECT_ROOT / "tools" / "build_product.ps1"


def test_batch_product_cli_verifies_bundle_before_delegating(monkeypatch) -> None:
    events: list[object] = []
    fake_root = Path("./portable/Symphony")

    monkeypatch.setattr(batch_product_cli, "compiled_bundle_root", lambda: fake_root)
    monkeypatch.setattr(batch_product_cli, "_manager", lambda: SimpleNamespace(public_key="public"))
    monkeypatch.setattr(
        batch_product_cli,
        "verify_manifest",
        lambda root, key: events.append(("verify", root, key)),
    )
    monkeypatch.setitem(
        sys.modules,
        "symphony_video_batch",
        SimpleNamespace(main=lambda: events.append("batch") or 17),
    )

    assert batch_product_cli.main() == 17
    assert events == [("verify", fake_root, "public"), "batch"]


def test_product_cli_verifies_bundle_before_machine_command(monkeypatch, capsys) -> None:
    events: list[object] = []
    fake_root = Path("./portable/Symphony")

    monkeypatch.setattr(sys, "argv", ["SymphonyLocal.exe", "machine"])
    monkeypatch.setattr(product_cli, "compiled_bundle_root", lambda: fake_root)
    monkeypatch.setattr(product_cli, "_manager", lambda: SimpleNamespace(public_key="public"))
    monkeypatch.setattr(
        product_cli,
        "verify_manifest",
        lambda root, key: events.append(("verify", root, key)),
    )
    monkeypatch.setattr(
        product_cli,
        "machine_code",
        lambda: events.append("machine") or "TEST-MACHINE",
    )

    assert product_cli.main() == 0
    assert events == [("verify", fake_root, "public"), "machine"]
    assert capsys.readouterr().out.strip() == "TEST-MACHINE"


def test_product_build_uses_portable_paths_and_sanitized_config() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert '[string]$OutputDir = ""' in script
    assert 'Join-Path $projectRoot "dist-product"' in script
    assert "%LOCALAPPDATA%\\Symphony" in script
    assert "OPENCLI_NODE_PATH" in script
    assert "OPENCLI_CLI_PATH" in script
    assert "SYMPHONY_BATCH_CONFIG" in script
    assert "SYMPHONY_LOCAL_CONFIG" in script
    assert "SYMPHONY_ASSET_CACHE" in script
    assert "SymphonyLocal.exe" in script
    assert "SymphonyBatch.exe" in script
    assert "uv run --isolated --python 3.12" in script
    assert '[string]$CompilerRoot = ""' in script
    assert "$env:SYMPHONY_BUILD_ROOT" in script
    assert '"./CodexBuild"' in script
    assert "npm.cmd ci --omit=dev --ignore-scripts" in script
    assert "symphony_video_batch.py" in script
    assert "canvas_video_batch.py" in script

    marker = "$apiConfig = @'"
    start = script.index(marker) + len(marker)
    end = script.index("'@", start)
    api_config = json.loads(script[start:end].strip())
    provider = api_config["providers"]["symphony_video"]
    assert provider == {
        "kind": "async_video_generation",
        "base_url": "http://127.0.0.1:8765",
        "auth": "none",
        "api_key": "",
    }


def test_product_is_signed_only_after_all_runtime_files_are_staged() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    sign_at = script.index("sign-bundle --root")
    assert sign_at > script.index("THIRD-PARTY-NOTICES.txt")
    assert sign_at > script.index("运行批处理.cmd")
    assert sign_at > script.index("extension\\manifest.json")
    assert script.index("Compress-Archive", sign_at) > sign_at


def test_product_defaults_use_bundle_and_local_app_data(monkeypatch, tmp_path) -> None:
    product = tmp_path / "product"
    local = tmp_path / "local"
    monkeypatch.setenv("SYMPHONY_PRODUCT_ROOT", str(product))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("OPENCLI_CLI_PATH", raising=False)
    monkeypatch.delenv("SYMPHONY_ASSET_CACHE", raising=False)

    settings = Settings.from_env()

    assert Path(settings.cli_path) == (
        product / "runtime" / "opencli" / "dist" / "src" / "main.js"
    )
    assert Path(settings.asset_cache_path) == local / "Symphony" / "asset_cache.json"
