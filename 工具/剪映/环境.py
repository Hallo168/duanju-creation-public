# -*- coding: utf-8 -*-
"""剪映相关脚本的公共入口：路径配置 + 加解密 + 草稿定位。

本项目**不依赖 ./AutoTool 的代码**（2026-07-27 用户裁定要项目独立）。
所有本机路径集中在同目录的 `剪映配置.json`，换机器只改那一个文件。

用法：
    from 环境 import 配置, 剪映草稿, 找草稿, 读草稿JSON, 写草稿JSON, ffprobe路径
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                       # 项目根目录
CONFIG_PATH = HERE / "剪映配置.json"

sys.path.insert(0, str(HERE))
import jy_crypto  # noqa: E402  同目录的加解密模块（从 AutoTool 搬入的副本）


def 配置() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"缺配置文件：{CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))


def 草稿根目录() -> Path:
    return Path(配置()["草稿根目录"])


def ffprobe路径() -> str:
    return str(配置().get("ffprobe") or "ffprobe")


def ffmpeg路径() -> str:
    return str(配置().get("ffmpeg") or "ffmpeg")


def 找草稿(名字: str) -> Path:
    """按草稿名定位；给的是绝对路径就直接用。找不到时列出候选，不猜。"""
    直接 = Path(名字)
    if 直接.is_absolute() and (直接 / "draft_content.json").exists():
        return 直接
    目标 = 草稿根目录() / 名字
    if (目标 / "draft_content.json").exists():
        return 目标
    候选 = sorted(p.name for p in 草稿根目录().iterdir() if (p / "draft_content.json").exists())
    sys.exit(f"草稿根目录里没有「{名字}」。现有草稿：\n  " + "\n  ".join(候选))


def 草稿文件(draft_dir: Path) -> list[Path]:
    """草稿的两份 draft_content.json：根目录一份、主时间线一份（剪映 10.x）。"""
    paths = []
    root = draft_dir / "draft_content.json"
    if root.exists():
        paths.append(root)
    project = draft_dir / "Timelines" / "project.json"
    if project.exists():
        timeline_id = json.loads(project.read_text(encoding="utf-8-sig")).get("main_timeline_id")
        sub = draft_dir / "Timelines" / str(timeline_id) / "draft_content.json"
        if timeline_id and sub.exists():
            paths.append(sub)
    if not paths:
        sys.exit(f"草稿里找不到 draft_content.json：{draft_dir}")
    return paths


def 是加密草稿(draft_dir: Path) -> bool:
    """整份草稿的格式判断。

    只看 draft_content.json 不够——它一旦被外部写成明文，判断就会自己把自己带偏；
    draft_meta_info.json 同属剪映加密，任一为密文就按加密草稿处理（后期规范 M14.1）。
    """
    probes = [*草稿文件(draft_dir), draft_dir / "draft_meta_info.json"]
    return any(p.exists() and jy_crypto.looks_encrypted(p.read_bytes()) for p in probes)


def 读草稿JSON(path: Path) -> dict:
    """读一份 draft_content.json，明文密文都吃。"""
    return json.loads(jy_crypto.load_text(path))


def 写草稿JSON(path: Path, data: dict, *, 加密: bool) -> None:
    """按原格式回写。

    加密草稿必须仍写密文：写成明文时剪映照常打开、视频字幕都在，
    **只有外部写入的音轨整条不出现且不报错**（后期规范 M14.1，21 集实测）。
    """
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    blob = jy_crypto.encrypt(payload).encode("latin-1") if 加密 else payload.encode("utf-8")
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_bytes(blob)
    tmp.replace(path)


def 音频时长(audio: str | Path) -> float:
    out = subprocess.run(
        [ffprobe路径(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(audio)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        sys.exit(f"取不到音频时长（ffprobe 路径对吗？）：{audio}")


def 用UTF8输出() -> None:
    """Windows 控制台默认 GBK，直接打印中文 JSON 会花屏。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
