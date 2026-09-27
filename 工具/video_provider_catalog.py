#!/usr/bin/env python3
"""Discover video-generation providers and their current models from api配置.json.

This is deliberately a read-only preflight: it never submits a chargeable job and
never prints credentials.  Video selection must use this command's live results,
not a hard-coded provider list or an old model name.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("api配置.json")
VIDEO_KINDS = {"video_generation", "async_video_generation"}


def read_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取统一 API 配置 {CONFIG_PATH}: {exc}") from exc


def endpoint_url(provider: dict, endpoint: str) -> str:
    value = str((provider.get("endpoints") or {}).get(endpoint) or "")
    if not value.startswith("GET "):
        raise RuntimeError("配置未提供 GET models 端点")
    values = {"base_url": str(provider.get("base_url") or ""), "models_base_url": str(provider.get("models_base_url") or provider.get("base_url") or "")}
    url = value[4:].format(**values)
    if url.startswith("/"):
        url = values["base_url"].rstrip("/") + url
    return url


def token_for(provider: dict) -> str:
    env_name = str(provider.get("credential_env_override") or "")
    value = os.environ.get(env_name) or str(provider.get("api_key") or "")
    if value:
        return value
    source = str(provider.get("credential_file") or "")
    if source:
        field = str(provider.get("credential_field") or "KEY")
        text = Path(source).read_text(encoding="utf-8-sig")
        if provider.get("credential_format") == "sk_token":
            matches = set(re.findall(r"\bsk-[A-Za-z0-9_.-]+", text))
            if len(matches) != 1:
                raise RuntimeError("凭据文件须包含唯一的 sk- 密钥")
            return matches.pop()
        found = re.search(r"(?im)^" + re.escape(field) + r"\s*[:：]\s*(\S+)", text)
        if not found:
            raise RuntimeError("凭据文件缺少指定密钥字段")
        return found.group(1)
    return ""


def request_models(provider: dict, timeout: float = 30) -> object:
    if provider.get("disabled") is True or provider.get("enabled") is False:
        raise RuntimeError("视频渠道已停用")
    url = endpoint_url(provider, "models")
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36",
    }
    # 本机回环 provider（如 symphony_video 的 127.0.0.1 代理）没有密钥，用 auth:"none" 声明。
    if str(provider.get("auth") or "").strip().lower() != "none":
        token = token_for(provider)
        if not token:
            raise RuntimeError("配置中没有可用密钥")
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        # Do not forward bearer credentials to a redirect destination.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        credential = headers.get("Authorization", "").removeprefix("Bearer ")
        if credential:
            detail = detail.replace(credential, "[REDACTED]")
        detail = detail[:300]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"网络请求失败: {exc}") from exc


def model_rows(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "models", "items", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for nested in ("models", "items", "list"):
                    if isinstance(value.get(nested), list):
                        return value[nested]
    return [payload]


def video_model_rows(payload: object) -> list[object]:
    """Keep video-capable rows when a general-purpose model endpoint exposes them."""
    rows = model_rows(payload)
    explicit_capabilities = any(isinstance(row, dict) and "supported_endpoint_types" in row for row in rows)
    explicit_type = any(isinstance(row, dict) and ("type" in row or "category" in row) for row in rows)
    if not explicit_capabilities and not explicit_type:
        return rows
    selected = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        capabilities = row.get("supported_endpoint_types") or []
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        capability_match = any("video" in str(value).lower() for value in capabilities)
        type_match = "video" in str(row.get("type") or row.get("category") or "").lower()
        if capability_match or type_match:
            selected.append(row)
    return selected


def compact_yfai_model(row: dict) -> dict:
    """Keep display/capability facts within the workbench subprocess output limit.

    Full parameter schemas are fetched independently by the submit executor.
    Descriptions and translated option labels otherwise repeat hundreds of KB.
    """
    result = {key: row[key] for key in ("id", "name", "display_name", "category", "type", "available", "deprecated") if key in row}
    result["description"] = str(row.get("description") or "")[:1200]
    kept = ("type", "enum", "default", "required", "min", "max", "minimum", "maximum", "max_items", "max_bytes", "max_edge_px", "available_when", "required_when")
    result["param_schema"] = {}
    for name, spec in (row.get("param_schema") or {}).items():
        if not isinstance(spec, dict):
            continue
        compact = {key: spec[key] for key in kept if key in spec}
        if "enum" not in compact and spec.get("options"):
            compact["enum"] = [option["value"] for option in spec["options"] if "value" in option]
        result["param_schema"][name] = compact
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="从 api配置.json 发现并实时查询视频模型")
    parser.add_argument("--provider", action="append", help="只查询指定 provider，可重复")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON（不含密钥）")
    parser.add_argument("--timeout", type=float, default=30, help="单个渠道的读取超时秒数")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout 必须为正数")
    providers = read_config().get("providers", {})
    disabled_requested = [
        name for name in args.provider or []
        if isinstance(providers.get(name), dict)
        and (providers[name].get("disabled") is True or providers[name].get("enabled") is False)
    ]
    if disabled_requested:
        parser.error("视频渠道已停用：" + "、".join(disabled_requested))
    selected = {
        name: value for name, value in providers.items()
        if isinstance(value, dict) and value.get("kind") in VIDEO_KINDS
        and value.get("disabled") is not True and value.get("enabled") is not False
        and (not args.provider or name in args.provider)
    }
    if not selected:
        parser.error("没有匹配到配置中的视频 provider")
    result = []
    status = 0
    for name, provider in selected.items():
        item = {"provider": name, "kind": provider.get("kind"), "base_url": provider.get("base_url")}
        try:
            payload = request_models(provider, timeout=args.timeout)
            if name == "meaicc_video":
                # MEAICC labels w-3 as openai, despite its video endpoint.
                item["models"] = [row for row in model_rows(payload)
                                  if isinstance(row, dict) and row.get("id") == provider.get("default_model")]
            else:
                item["models"] = video_model_rows(payload)
            if name == "yfai_video":
                item["models"] = [compact_yfai_model(row) for row in item["models"]]
        except RuntimeError as exc:
            item["error"] = str(exc)
            status = 1
        result.append(item)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result:
            print(f"PROVIDER {item['provider']} kind={item['kind']} base_url={item['base_url']}")
            if "error" in item:
                print(f"  ERROR {item['error']}")
            else:
                for row in item["models"]:
                    print("  MODEL " + json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
