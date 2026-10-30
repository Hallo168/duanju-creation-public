# -*- coding: utf-8 -*-
"""配乐库体检：扫描 配乐库/ 下所有音频，测时长、整体响度(LUFS)、开头静音，产出索引。

用法：
  python 工具/配乐/配乐库索引.py                       # 全量重扫，写 配乐库/索引.json + 索引.md
  python 工具/配乐/配乐库索引.py --only BGM            # 只扫某个子目录

为什么要测 LUFS：库内曲目自身响度能差 7.5 dB（-9.9 ~ -17.4 LUFS 实测），
所以方案里手填 volume 数字得到的听感是飘的。配乐方案生成.py 用这里的 LUFS
反算 volume，让每段 BGM 落在同一个目标电平上。见 .claude/skills/houqi/知识/后期规范.md M5。

人工填写的字段（情绪标签/适用段落/来源/许可）在重扫时**原样保留**，只刷新测量值。
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "配乐库"
INDEX_JSON = LIBRARY / "索引.json"
INDEX_MD = LIBRARY / "索引.md"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "剪映"))
from 环境 import ffmpeg路径, ffprobe路径  # noqa: E402
FFMPEG = Path(ffmpeg路径())
FFPROBE = Path(ffprobe路径())
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

# 人工维护、重扫不覆盖的字段
MANUAL_FIELDS = ("情绪标签", "适用段落", "来源", "许可", "备注")


def _run(args):
    return subprocess.run(
        [str(item) for item in args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def probe_duration(path: Path) -> float:
    result = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=nw=1:nk=1", path])
    try:
        return round(float(result.stdout.strip()), 3)
    except ValueError:
        return 0.0


def probe_loudness(path: Path) -> dict:
    """整轨积分响度与响度范围（EBU R128）。"""
    result = _run([FFMPEG, "-hide_banner", "-nostats", "-i", path,
                   "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    text = result.stderr
    lufs = re.search(r"I:\s*(-?\d+\.?\d*)\s*LUFS", text)
    lra = re.search(r"LRA:\s*(-?\d+\.?\d*)\s*LU", text)
    return {
        "lufs": float(lufs.group(1)) if lufs else None,
        "lra": float(lra.group(1)) if lra else None,
    }


def probe_peak(path: Path) -> float | None:
    """峰值电平 dBFS。短音效不足 3 秒时积分响度不可靠（门限吃掉），改看峰值。"""
    result = _run([FFMPEG, "-hide_banner", "-nostats", "-i", path,
                   "-af", "volumedetect", "-f", "null", "-"])
    peak = re.search(r"max_volume:\s*(-?\d+\.?\d*)\s*dB", result.stderr)
    return float(peak.group(1)) if peak else None


def probe_head_silence(path: Path) -> float:
    """开头静音时长——换乐点撞上前奏空档会听成'掉音'，据此给 source_start 默认值。"""
    result = _run([FFMPEG, "-hide_banner", "-nostats", "-t", "40", "-i", path,
                   "-af", "silencedetect=n=-38dB:d=0.4", "-f", "null", "-"])
    starts = re.findall(r"silence_start:\s*(-?\d+\.?\d*)", result.stderr)
    ends = re.findall(r"silence_end:\s*(-?\d+\.?\d*)", result.stderr)
    if starts and ends and abs(float(starts[0])) < 0.05:
        return round(float(ends[0]), 3)
    return 0.0


def scan(only: str | None = None) -> dict:
    if not (FFMPEG.is_file() or shutil.which(str(FFMPEG))) or not (FFPROBE.is_file() or shutil.which(str(FFPROBE))):
        sys.exit(f"找不到 ffmpeg/ffprobe：{FFMPEG} / {FFPROBE}")

    previous = {}
    if INDEX_JSON.exists():
        previous = {
            item["文件"]: item
            for item in json.loads(INDEX_JSON.read_text(encoding="utf-8-sig")).get("曲目", [])
        }

    entries = []
    for path in sorted(LIBRARY.rglob("*")):
        if path.suffix.lower() not in AUDIO_SUFFIXES or not path.is_file():
            continue
        relative = path.relative_to(LIBRARY).as_posix()
        if only and not relative.startswith(f"{only}/"):
            continue
        print(f"测量 {relative} ...", flush=True)
        loudness = probe_loudness(path)
        entry = {
            "文件": relative,
            "分类": relative.split("/")[0],
            "绝对路径": path.as_posix(),
            "时长s": probe_duration(path),
            "响度LUFS": loudness["lufs"],
            "响度范围LU": loudness["lra"],
            "峰值dBFS": probe_peak(path),
            "开头静音s": probe_head_silence(path),
        }
        old = previous.get(relative, {})
        for field in MANUAL_FIELDS:
            entry[field] = old.get(field, "")
        entries.append(entry)

    # 只扫了一部分时，保留其余旧记录
    if only:
        scanned = {item["文件"] for item in entries}
        entries.extend(item for key, item in previous.items() if key not in scanned)
        entries.sort(key=lambda item: item["文件"])

    return {
        "说明": "配乐库测量索引；情绪标签/适用段落/来源/许可为人工维护字段，重扫不覆盖。",
        "曲目": entries,
    }


def write_markdown(data: dict) -> None:
    lines = [
        "# 配乐库索引（机器测量 + 人工标注）",
        "",
        "> 测量值由 `工具/配乐/配乐库索引.py` 重扫生成，**不要手改**；情绪标签/适用段落/来源/许可为人工维护列，重扫会保留。",
        "> `建议起点` = 开头静音时长，配乐方案生成时默认从这里起截，避免换乐点撞上空档。",
        "> 用法见 `.claude/skills/houqi/知识/后期规范.md` 第二节（配乐音效 M 编号）。",
        "",
    ]
    for category in sorted({item["分类"] for item in data["曲目"]}):
        lines += [
            f"## {category}",
            "",
            "| 文件 | 时长s | 响度LUFS | 峰值dBFS | 动态LU | 建议起点s | 情绪标签 | 适用段落 | 来源/许可 |",
            "|---|---:|---:|---:|---:|---:|---|---|---|",
        ]
        for item in data["曲目"]:
            if item["分类"] != category:
                continue
            source = " / ".join(part for part in (item.get("来源", ""), item.get("许可", "")) if part)
            lines.append(
                "| {文件} | {时长} | {lufs} | {peak} | {lra} | {head} | {tag} | {use} | {src} |".format(
                    文件=Path(item["文件"]).name,
                    时长=f'{item["时长s"]:.1f}',
                    lufs="—" if item["响度LUFS"] is None else f'{item["响度LUFS"]:.1f}',
                    peak="—" if item.get("峰值dBFS") is None else f'{item["峰值dBFS"]:.1f}',
                    lra="—" if item["响度范围LU"] is None else f'{item["响度范围LU"]:.1f}',
                    head=f'{item["开头静音s"]:.2f}',
                    tag=item.get("情绪标签", "") or "—",
                    use=item.get("适用段落", "") or "—",
                    src=source or "—",
                )
            )
        lines.append("")
    INDEX_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    data = scan(only)
    INDEX_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(data)
    print(f"曲目 {len(data['曲目'])} 条 -> {INDEX_JSON}")
    print(f"                 -> {INDEX_MD}")


if __name__ == "__main__":
    main()
