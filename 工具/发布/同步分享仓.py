# -*- coding: utf-8 -*-
"""把主仓库的可分享工具层导出到分享仓库（默认 G:\\短剧工具箱）。

用法：  python 工具/发布/同步分享仓.py [目标目录]

设计（2026-08-02 裁定，两仓库制）：
- 主仓库（G:\\短剧创作）永远私有——git 历史里有明文密钥与创作资产，不对外。
- 分享仓库 = 本脚本从主仓库单向导出的工具层快照，全新 git 历史，天生无密钥。
- 白名单进、黑名单兜底；api配置.json / symphony配置.json 导出前自动脱敏。
- 重复运行 = 增量同步：先清空目标（保留 .git），再全量重拷，git diff 自然呈现变化。

不进分享仓的：stories/、knowledge/、产品图/、配乐库/、爆款结构库、洗稿线、
所有凭据文件（chatgpt2api/config.json、flow2api/config/setting.toml、账号快照、许可证）。
"""
import json
import re
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]          # <主仓库根>
DEST = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"<分享仓根>")

# 任何位置都不拷的目录/文件名
IGNORE_NAMES = {
    "node_modules", ".venv", "__pycache__", ".next", ".git", ".pytest_cache", "logs",
    ".hermes-tmp.1763", "运行日志.tmp", "运行日志.tmp.err",
}
# 相对主仓库根的黑名单前缀（凭据与运行时数据，绝不外传）
BLOCK_PREFIXES = [
    "外部依赖/chatgpt2api/config.json",
    "外部依赖/chatgpt2api/.env",
    "外部依赖/chatgpt2api/data",
    "外部依赖/chatgpt2api/logs",
    "外部依赖/flow2api/config/setting.toml",
    "外部依赖/flow2api/config/setting_warp.toml",
    "外部依赖/flow2api/data",
    "外部依赖/flow2api/tmp",
    "外部依赖/flow2api/generated",
    "外部依赖/flow2api/output",
    "外部依赖/flow2api/src/tmp",
    "外部依赖/flow2api/logs.txt",
    "工具/symphony/data",            # 账号快照 / 许可证 / 资产缓存
    "工具/api配置.json",              # 用脱敏版替代
    "工具/symphony/symphony配置.json",  # 用脱敏版替代
    "工具/发布",                      # 导出脚本本身不进分享仓
]
# 白名单：相对主仓库根，目录整拷或单文件
COPY_ITEMS = [
    "外部依赖",
    "工具/symphony",
    "工具/剪映",
    "工具/配乐",
    "工具/12ai_video_batch.py",
    "工具/canvas_video_batch.py",
    "工具/guagu_video_batch.py",
    "工具/symphony_video_batch.py",
    "工具/video_provider_catalog.py",
    "工具/gen_grids_multi.py",
    "工具/字幕提取.py",
    "工具/字幕回写.py",
    "工具/全景取景器.html",
]
# 用户账号别名 → 中性占位（正则，作用于脱敏后的配置文本）
ALIAS_SCRUB = [
    (r"main-a", "main-a"),
    (r"main-b", "main-b"),
    (r"(?<![A-Za-z0-9_])main-a(?![A-Za-z0-9_])", "main-a"),
    (r"(?<![A-Za-z0-9_])main-b(?![A-Za-z0-9_])", "main-b"),
    (r"(?<![A-Za-z0-9_])main-c(?![A-Za-z0-9_])", "main-c"),
    (r"(?<![A-Za-z0-9_])main-d(?![A-Za-z0-9_])", "main-d"),
    (r"(?<![A-Za-z0-9_])main-e(?![A-Za-z0-9_])", "main-e"),
    (r"legacy-a", "legacy-a"),
    (r"main-a", "main-a"),
    (r"main-b", "main-b"),
    (r"main-c", "main-c"),
    (r"main-d", "main-d"),
    (r"main-e", "main-e"),
]


LOCAL_PATH_SCRUB = [
    (r"(?i)G:[\\/]短剧创作", "<主仓库根>"),
    (r"(?i)G:[\\/]短剧工具箱", "<分享仓根>"),
    (r"(?i)F:[\\/]OpenCLI-Symphony", "<OpenCLI-Symphony>"),
    (r"(?i)F:[\\/]tiktok-symphony-proxy", "<SymphonyProxy>"),
    (r"(?i)C:[\\/]Users[\\/]Administrator", "<用户目录>"),
]


def blocked(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    return any(rel == p or rel.startswith(p + "/") for p in BLOCK_PREFIXES)


def copy_tree(src: Path, dest: Path, rel_base: str) -> None:
    for item in src.iterdir():
        rel = f"{rel_base}/{item.name}" if rel_base else item.name
        if item.name in IGNORE_NAMES or blocked(rel):
            continue
        if item.is_file() and item.suffix.lower() in {".log", ".err"}:
            continue
        target = dest / item.name
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            copy_tree(item, target, rel)
        else:
            shutil.copy2(item, target)


def scrub_aliases(text: str) -> str:
    for pat, repl in ALIAS_SCRUB:
        text = re.sub(pat, repl, text)
    return text


def scrub_local_paths(text: str) -> str:
    for pat, repl in LOCAL_PATH_SCRUB:
        text = re.sub(pat, repl, text)
    return text


def scrub_sensitive_source(rel: str, text: str) -> str:
    if rel == "外部依赖/flow2api/src/services/flow_client.py":
        text, count = re.subn(
            r'(?m)^(\s*FLOW_PUBLIC_API_KEY\s*=\s*)["\'][^"\']*["\']',
            r'\1os.getenv("FLOW_PUBLIC_API_KEY", "")',
            text,
        )
        if count and "import os\n" not in text:
            text = text.replace("import asyncio\n", "import os\nimport asyncio\n", 1)
    if rel == "外部依赖/chatgpt2api/scripts/test_outlook_token_mailbox.py":
        text = re.sub(
            r'(?m)(--file"\s*,\s*default=r["\'])[^"\']*(["\'])',
            r'\1<凭据文件路径>\2',
            text,
        )
    if rel == "外部依赖/OpenCLI-Symphony/clis/tiktok-symphony/direct-session.js":
        text = re.sub(
            r"(?m)(DEFAULT_PROXY_DIR\s*=\s*['\"])[^'\"]*(['\"])",
            r"\1<本机路径>\2",
            text,
        )
    if rel == "外部依赖/OpenCLI-Symphony/clis/tiktok-symphony/utils.js":
        text = re.sub(
            r"(?m)(DEFAULT_DIRECT_ASSET_CACHE\s*=\s*['\"])[^'\"]*(['\"])",
            r"\1<本机路径>\2",
            text,
        )
    if rel == "工具/剪映/剪映配置.json":
        try:
            cfg = json.loads(text)
            for key in ("草稿根目录", "剪映安装目录", "ffmpeg", "ffprobe"):
                if key in cfg:
                    cfg[key] = ""
            text = json.dumps(cfg, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
    return text


def scrub_text(rel: str, text: str) -> str:
    return scrub_sensitive_source(rel, scrub_local_paths(scrub_aliases(text)))


def blank_api_keys(node):
    if isinstance(node, dict):
        return {
            k: ("" if k in {"api_key", "api-key"} and isinstance(v, str) else blank_api_keys(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [blank_api_keys(v) for v in node]
    return node


def export_api_config() -> None:
    cfg = json.loads((SRC / "工具/api配置.json").read_text(encoding="utf-8"))
    cfg = blank_api_keys(cfg)
    text = scrub_text("工具/api配置.json", json.dumps(cfg, ensure_ascii=False, indent=2))
    out = DEST / "工具/api配置.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")


def export_symphony_config() -> None:
    cfg = json.loads((SRC / "工具/symphony/symphony配置.json").read_text(encoding="utf-8"))
    cfg["OpenCLI入口"] = "外部依赖/OpenCLI-Symphony/dist/src/main.js"
    cfg["ffprobe"] = "ffprobe"
    cfg["默认主账号"] = None
    text = scrub_text(
        "工具/symphony/symphony配置.json",
        json.dumps(cfg, ensure_ascii=False, indent=2),
    )
    out = DEST / "工具/symphony/symphony配置.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for item in DEST.iterdir():                     # 清空旧内容，保留 .git
        if item.name == ".git":
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()

    for entry in COPY_ITEMS:
        src = SRC / entry
        if not src.exists():
            print(f"[缺失跳过] {entry}")
            continue
        dest = DEST / entry
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            copy_tree(src, dest, entry.replace("\\", "/"))
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        print(f"[已拷] {entry}")

    export_api_config()
    print("[脱敏] 工具/api配置.json（api_key 全部置空、账号别名与本机路径抹除）")
    export_symphony_config()
    print("[脱敏] 工具/symphony/symphony配置.json（账号别名置空、路径改相对）")

    # 全仓文本后处理：账号别名等个人标识不随代码/文档外泄
    text_exts = {
        ".py", ".md", ".json", ".cmd", ".toml", ".txt", ".html", ".yml", ".yaml",
        ".js", ".mjs", ".cjs", ".ts", ".tsx", ".ps1", ".psm1",
    }
    scrubbed = 0
    for f in DEST.rglob("*"):
        if f.is_file() and f.suffix.lower() in text_exts:
            raw = f.read_text(encoding="utf-8", errors="ignore")
            rel = f.relative_to(DEST).as_posix()
            new = scrub_text(rel, raw)
            if new != raw:
                f.write_text(new, encoding="utf-8")
                scrubbed += 1
    print(f"[抹除] 账号别名后处理，改写 {scrubbed} 个文件")

    tmpl = SRC / "工具/发布/分享仓README.md"
    if tmpl.exists():
        shutil.copy2(tmpl, DEST / "README.md")
    (DEST / ".gitignore").write_text(
        (SRC / "外部依赖/.gitignore").read_text(encoding="utf-8").replace(
            "chatgpt2api/", "外部依赖/chatgpt2api/"
        ).replace("flow2api/", "外部依赖/flow2api/")
        + "\n# symphony 运行态\n工具/symphony/data/\n*.log\n",
        encoding="utf-8",
    )
    print(f"\n导出完成 → {DEST}")


if __name__ == "__main__":
    main()
