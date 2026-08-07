"""Start the TikTok Symphony proxy using paths from symphony配置.json.

Run via 启动代理.cmd (or: uv run python launch.py). Reads the local-machine
config, injects the environment variables app/config.py expects, warns when the
port disagrees with 工具/api配置.json, then serves app.main:app on 127.0.0.1.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
CONFIG_PATH = HERE / "symphony配置.json"
API_CONFIG_PATH = HERE.parent / "api配置.json"
PROVIDER_NAME = "symphony_video"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"缺少配置文件：{CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def resolve_repo_path(value: str) -> Path:
    """Resolve a config path relative to the share/repository root."""
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def check_port_agreement(port: int) -> None:
    """api配置.json 是执行器读的，symphony配置.json 是服务端读的，两边端口必须一致。"""
    if not API_CONFIG_PATH.exists():
        return
    try:
        providers = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8")).get("providers", {})
        base_url = providers.get(PROVIDER_NAME, {}).get("base_url", "")
    except (json.JSONDecodeError, AttributeError):
        return
    if base_url and f":{port}" not in base_url:
        print(
            f"[警告] 端口不一致：symphony配置.json 用 {port}，"
            f"但 api配置.json 的 {PROVIDER_NAME}.base_url 是 {base_url}。"
            "执行器会连错地方，请把两处改成同一个端口。",
            file=sys.stderr,
        )


def main() -> None:
    cfg = load_config()
    port = int(cfg.get("监听端口", 8765))

    os.environ.setdefault("OPENCLI_NODE_PATH", str(cfg.get("node") or "node"))
    cli_path = cfg.get("OpenCLI入口")
    if cli_path:
        resolved_cli_path = resolve_repo_path(str(cli_path))
        os.environ.setdefault("OPENCLI_CLI_PATH", str(resolved_cli_path))
        if not resolved_cli_path.exists():
            print(
                f"[提示] OpenCLI 入口不存在：{resolved_cli_path}。"
                "直连（提交/轮询/下载）仍可用；首次登录、新参考图首次上传会失败。",
                file=sys.stderr,
            )
    os.environ.setdefault("SYMPHONY_ASSET_CACHE", str(HERE / "data" / "asset_cache.json"))
    os.environ.setdefault("SYMPHONY_LICENSE_PUBLIC_KEY", str(HERE / "app" / "license_public_key.pem"))
    os.environ.setdefault("SYMPHONY_DIRECT_ONLY", "0")

    check_port_agreement(port)

    import uvicorn

    print(f"TikTok Symphony 代理启动中 → http://127.0.0.1:{port}  (Swagger: /docs)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, app_dir=str(HERE))


if __name__ == "__main__":
    main()
