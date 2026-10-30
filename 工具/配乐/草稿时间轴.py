# -*- coding: utf-8 -*-
"""剪映草稿时间轴导出 + 单元对齐：给配乐方案提供"哪一秒是哪个单元、哪里能换乐"。

用法：
  python 工具/配乐/草稿时间轴.py "<草稿目录>" --fenjing "<分镜提示词.md>" --out "<输出JSON>"
  python 工具/配乐/草稿时间轴.py "<草稿目录>"                 # 只导字幕表，不做单元对齐

为什么要对齐：草稿里视频轨是「复合片段」，拿不到单元边界；能用的锚点只有
①字幕轨（ASR 已按 后期规范 §一 校对成与台词稿逐字一致）②分镜提示词.md 的台词与分镜总表。
把两边的台词字流做序列比对，就能把每条字幕归到单元，进而得到单元时间区间和
"硬切"边界——换乐点首选落在这些边界上（.claude/skills/houqi/知识/后期规范.md M2）。

加解密与草稿定位走 工具/剪映/环境.py（明文密文都读；2026-07-27 项目独立化）。
"""
import argparse
import difflib
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "剪映"))
from 环境 import 读草稿JSON, 找草稿, 草稿文件  # noqa: E402

GAP_MIN_S = 0.4          # 视为"可换乐间隙"的最短字幕空档
KEEP = re.compile(r"[一-鿿A-Za-z0-9]")


def normalize(text: str) -> str:
    return "".join(KEEP.findall(text or ""))


def load_subtitles(draft_dir: Path) -> tuple[float, list[dict]]:
    candidates = [读草稿JSON(path) for path in 草稿文件(draft_dir)]
    data = next(
        (item for item in candidates if item.get("materials", {}).get("texts")),
        candidates[0],
    )
    texts = {item["id"]: item for item in data.get("materials", {}).get("texts", [])}
    rows = []
    for track in data.get("tracks", []):
        if track.get("type") != "text":
            continue
        for segment in track.get("segments", []):
            material = texts.get(segment.get("material_id"), {})
            if material.get("type") != "subtitle":
                continue
            try:
                content = json.loads(material.get("content", "{}")).get("text", "")
            except (json.JSONDecodeError, TypeError):
                content = material.get("recognize_text", "")
            timerange = segment.get("target_timerange", {})
            rows.append(
                {
                    "start": timerange.get("start", 0) / 1e6,
                    "end": (timerange.get("start", 0) + timerange.get("duration", 0)) / 1e6,
                    "text": content,
                }
            )
    rows.sort(key=lambda row: row["start"])
    for index, row in enumerate(rows):
        row["i"] = index
        row["start"] = round(row["start"], 3)
        row["end"] = round(row["end"], 3)
    # 向下取整到毫秒：末段结束点直接拿这个值，四舍五入会超出草稿总长半毫秒被写入脚本拒收
    duration = math.floor(int(data.get("duration") or 0) / 1000) / 1000
    return duration, rows


def parse_fenjing(path: Path) -> tuple[list[dict], dict]:
    """从 分镜提示词.md 取每个单元的台词序列，以及分镜总表里的衔接/情绪/风险列。"""
    raw = path.read_text(encoding="utf-8-sig")

    summary = {}
    for line in raw.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        unit_cell = re.match(r"^U?(\d+(?:\.\d+)?)(?:\s|$)", cells[1], re.I) if len(cells) >= 9 else None
        if unit_cell:
            raw_number = unit_cell.group(1)
            number = float(raw_number) if "." in raw_number else int(raw_number)
            if len(cells) >= 11:
                summary[number] = {
                    "秒数预算": cells[3],
                    "情绪终点": cells[6],
                    "衔接": cells[8],
                    "风险": cells[9],
                }
            else:
                summary[number] = {
                    "秒数预算": cells[3],
                    "情绪终点": cells[5],
                    "衔接": cells[7],
                    "风险": cells[8],
                }

    units = []
    for match in re.finditer(r"^##\s*单元(\d+(?:\.\d+)?)[^\n]*\n(.*?)(?=^##\s|\Z)", raw, re.S | re.M):
        raw_number = match.group(1)
        number = float(raw_number) if "." in raw_number else int(raw_number)
        block = match.group(2)
        # 兼容三代分镜台词格式：旧版 {台词}；新版逐镜 bullet
        # 「- 角色（声线说明）“台词”」；导演分镜万相版「角色说道，…：“台词”」。
        lines = re.findall(r"^\s*-\s*[^\n“]+）“([^”]*)”\s*$", block, re.M)
        if not lines:
            lines = re.findall(r'(?:[一-龥]{1,6}道|说道|回答|喊道|叫道|问道|喝道|反问|继续说)[^\n：:]*[：:]\s*[“"]([^”"]*)[”"]', block)
        if not lines:
            lines = re.findall(r"\{([^{}]*)\}", block)
        units.append(
            {
                "单元": number,
                "台词": lines,
                **summary.get(number, {"秒数预算": "", "情绪终点": "", "衔接": "", "风险": ""}),
            }
        )
    units.sort(key=lambda unit: unit["单元"])
    return units, summary


def align(units: list[dict], subtitles: list[dict]) -> dict:
    """台词字流 ↔ 字幕字流序列比对，把每条字幕归到一个单元。"""
    script, script_owner = [], []
    for unit in units:
        for line in unit["台词"]:
            for char in normalize(line):
                script.append(char)
                script_owner.append(unit["单元"])

    subs, subs_owner = [], []
    for row in subtitles:
        for char in normalize(row["text"]):
            subs.append(char)
            subs_owner.append(row["i"])

    votes: dict[int, dict[int, int]] = {row["i"]: {} for row in subtitles}
    matched_chars = 0
    matcher = difflib.SequenceMatcher(None, "".join(script), "".join(subs), autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            unit_number = script_owner[block.a + offset]
            subtitle_index = subs_owner[block.b + offset]
            votes[subtitle_index][unit_number] = votes[subtitle_index].get(unit_number, 0) + 1
            matched_chars += 1

    for row in subtitles:
        tally = votes[row["i"]]
        row["单元"] = max(tally, key=tally.get) if tally else None

    # 单元起点 = 归属它的第一条字幕；末尾用下一个单元的起点补齐，保证时间轴无缝
    first_start = {}
    for row in subtitles:
        if row["单元"] is not None and row["单元"] not in first_start:
            first_start[row["单元"]] = row["start"]

    ordered = [unit["单元"] for unit in units if unit["单元"] in first_start]
    ranges = {}
    for position, number in enumerate(ordered):
        start = first_start[number]
        end = first_start[ordered[position + 1]] if position + 1 < len(ordered) else None
        ranges[number] = {"start": start, "end": end}

    return {
        "字幕匹配率": round(matched_chars / max(len(subs), 1), 4),
        "台词覆盖率": round(matched_chars / max(len(script), 1), 4),
        "缺席单元": [unit["单元"] for unit in units if unit["单元"] not in first_start],
        "未归属字幕": [row["i"] for row in subtitles if row["单元"] is None],
        "单元区间": ranges,
    }


def find_gaps(subtitles: list[dict], duration: float) -> list[dict]:
    gaps = []
    for previous, current in zip(subtitles, subtitles[1:]):
        length = current["start"] - previous["end"]
        if length >= GAP_MIN_S:
            gaps.append(
                {
                    "start": round(previous["end"], 3),
                    "end": round(current["start"], 3),
                    "长度": round(length, 3),
                    "前一条字幕": previous["i"],
                }
            )
    if subtitles and duration - subtitles[-1]["end"] >= GAP_MIN_S:
        gaps.append(
            {
                "start": round(subtitles[-1]["end"], 3),
                "end": round(duration, 3),
                "长度": round(duration - subtitles[-1]["end"], 3),
                "前一条字幕": subtitles[-1]["i"],
            }
        )
    return gaps


def snap(time_point: float, gaps: list[dict], tolerance: float = 2.0) -> dict:
    """把换乐点吸附到最近的字幕间隙中点；找不到就原样返回并标记。"""
    best, best_distance = None, None
    for gap in gaps:
        center = (gap["start"] + gap["end"]) / 2
        distance = abs(center - time_point)
        if best_distance is None or distance < best_distance:
            best, best_distance = gap, distance
    if best is None or best_distance > tolerance:
        return {"吸附": False, "时间": round(time_point, 3), "偏移": 0.0}
    center = round((best["start"] + best["end"]) / 2, 3)
    return {"吸附": True, "时间": center, "偏移": round(center - time_point, 3)}


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="剪映草稿时间轴导出 + 单元对齐")
    parser.add_argument("draft_dir")
    parser.add_argument("--fenjing", default="", help="分镜提示词.md 路径；给了才做单元对齐")
    parser.add_argument("--out", default="", help="输出 JSON 路径；不给则打印到 stdout")
    args = parser.parse_args()

    draft_dir = 找草稿(args.draft_dir)
    duration, subtitles = load_subtitles(draft_dir)
    gaps = find_gaps(subtitles, duration)
    result = {
        "草稿": str(draft_dir),
        "草稿时长s": duration,
        "字幕条数": len(subtitles),
        "可换乐间隙数": len(gaps),
        "字幕": subtitles,
        "间隙": gaps,
    }

    if args.fenjing:
        units, _ = parse_fenjing(Path(args.fenjing))
        alignment = align(units, subtitles)
        rows = []
        for unit in units:
            span = alignment["单元区间"].get(unit["单元"])
            if not span:
                continue
            start = span["start"]
            end = span["end"] if span["end"] is not None else round(duration, 3)
            row = {
                "单元": unit["单元"],
                "start": round(start, 3),
                "end": round(end, 3),
                "时长s": round(end - start, 3),
                "衔接": unit["衔接"],
                "情绪终点": unit["情绪终点"],
                "风险": unit["风险"],
                "台词条数": len(unit["台词"]),
            }
            if "硬切" in unit["衔接"]:
                row["换乐候选"] = snap(start, gaps)
            rows.append(row)
        result["单元"] = rows
        result["对齐质量"] = {
            key: value for key, value in alignment.items() if key != "单元区间"
        }
        result["换乐候选点"] = [
            {"单元": row["单元"], "衔接": row["衔接"], **row["换乐候选"]}
            for row in rows
            if "换乐候选" in row
        ]

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        summary = {key: value for key, value in result.items()
                   if key not in ("字幕", "间隙", "单元")}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"-> {args.out}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
