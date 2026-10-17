# -*- coding: utf-8 -*-
"""段落表 → 剪映配乐方案 JSON（音量按响度反算，不手填）。

用法：
  python 工具/配乐/配乐方案生成.py "<段落表.json>"

段落表由 agent 按 .claude/skills/houqi/知识/后期规范.md 写，只填"哪段是什么情绪、用哪首曲子"，
音量/起截点/淡化由本脚本算。段落起点二选一：`起单元`（取单元边界并吸附字幕间隙）
或 `绝对时间`（秒，精确钉在情绪句上——2026-08-06 三段式骨架的主用法）；
中间对白连续铺乐；`"类型": "静默"`（可无曲目）仅用于M4片尾静默收，放在末条：

{
  "草稿": "./JianyingPro Drafts/21-还有口气",
  "时间轴": "工具/运行垃圾/tasks/21/时间轴.json",
  "对白音频": "./stories/21-还有口气/21-还有口气.mp4",
  "输出目录": "工具/运行垃圾/tasks/21",
  "段落": [
    {"label": "开场焦虑", "绝对时间": 3.9, "类型": "压迫", "曲目": "BGM/剧情纠结焦虑紧张 Especially main version.mp3"},
    {"label": "主床进场", "绝对时间": 41.2, "类型": "低谷", "曲目": "BGM/Asphyxia(窒息).mp3"},
    {"label": "尾曲跳副歌", "起单元": 27, "类型": "打脸", "曲目": "BGM/Asphyxia(窒息).mp3", "source_start": 147.5}
  ],
  "环境": [
    {"label": "写字楼大堂", "起单元": 1, "止单元": 2, "文件": "音效/环境-大堂人流脚步-Mixkit.wav"}
  ],
  "音效": [
    {"label": "「你数数」·点钞", "绝对时间": 18.3, "文件": "音效/动作-翻点钞票-Mixkit.wav", "类型": "拟音"},
    {"label": "人海反转", "单元": 26, "偏移": 0.0, "文件": "音效/爆点-反转低频重击-Mixkit.wav", "类型": "爆点"}
  ]
}

三层产物：配乐方案.json（BGM 一条轨）、环境方案.json（环境床一条轨）、
音效方案-1/2/3….json（音效按重叠自动分轨），外加 配乐方案.md 供人审核。

音量口径（规范 M5/M7）：BGM 与环境床走 LUFS 口径——目标电平 = 该段对白响度 − Δ(类型)，
volume = 10^((目标 − 素材自身LUFS)/20)；音效走峰值口径。首集出片后按实听回校 Δ 表。
"""
import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "配乐库"
INDEX_JSON = LIBRARY / "索引.json"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "剪映"))
from 环境 import ffmpeg路径  # noqa: E402
FFMPEG = ffmpeg路径()

# 段落类型 → BGM 压在对白之下多少 LU（规范 M5；数值为项目起点值，首集实测后回校）
DELTA_BGM = {
    "对白": 15.0,
    "压迫": 13.0,
    "悬疑": 13.0,
    "回忆": 14.0,
    "低谷": 14.0,
    "蓄势": 12.0,
    "打脸": 10.0,
    "温情": 14.0,
    "口播": 18.0,
    "过场": 8.0,
    "片尾": 12.0,
    "环境": 22.0,   # 环境底噪层：铺满整场、只垫不抢，压在 BGM 之下
}
# 音效类型 → 峰值相对对白峰值的差值（规范 M8）
DELTA_SFX = {"爆点": 0.0, "强调": -8.0, "桥接": -6.0, "氛围": -12.0, "拟音": -16.0}

VOLUME_FLOOR, VOLUME_CEIL = 0.02, 1.0
FADE_IN_DEFAULT, FADE_OUT_DEFAULT, FADE_OUT_LAST = 0.8, 1.2, 2.5
SFX_BRIDGE_PREROLL = 0.4     # 桥接音效提前量：盖住换乐硬切点
LANE_GUARD = 0.01            # 同轨相邻两段之间强制留的间隙（秒），防剪映吞段


def _run(args) -> str:
    result = subprocess.run(
        [str(item) for item in args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return result.stderr


def measure_window(audio: Path, start: float, duration: float) -> float | None:
    """量某个时间窗的积分响度——用来知道"这一段对白有多响"。"""
    text = _run([FFMPEG, "-hide_banner", "-nostats", "-ss", f"{start:.3f}",
                 "-t", f"{max(duration, 1.0):.3f}", "-i", audio,
                 "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    match = re.search(r"./s*(-?\d+\.?\d*)\s*LUFS", text)
    return float(match.group(1)) if match else None


def measure_peak(audio: Path) -> float | None:
    text = _run([FFMPEG, "-hide_banner", "-nostats", "-i", audio,
                 "-af", "volumedetect", "-f", "null", "-"])
    match = re.search(r"max_volume:\s*(-?\d+\.?\d*)\s*dB", text)
    return float(match.group(1)) if match else None


def gain_to_volume(target_db: float, source_db: float) -> tuple[float, bool]:
    """返回 (剪映音量, 是否被 0~1 夹住)。夹住意味着素材本身太轻/太响，够不到目标电平。"""
    raw = 10 ** ((target_db - source_db) / 20)
    volume = min(VOLUME_CEIL, max(VOLUME_FLOOR, raw))
    return round(volume, 4), not math.isclose(raw, volume, rel_tol=1e-6)


def load_index() -> dict:
    if not INDEX_JSON.exists():
        sys.exit("缺 配乐库/索引.json，先跑 工具/配乐/配乐库索引.py")
    data = json.loads(INDEX_JSON.read_text(encoding="utf-8-sig"))
    return {item["文件"]: item for item in data["曲目"]}


def resolve(path_value: str, base: Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else (base / path)


def build(spec_path: Path) -> dict:
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    index = load_index()
    timeline = json.loads(resolve(spec["时间轴"], ROOT).read_text(encoding="utf-8-sig"))
    duration = timeline["草稿时长s"]
    units = {row["单元"]: row for row in timeline.get("单元", [])}
    if not units:
        sys.exit("时间轴 JSON 里没有单元对齐结果，先用 草稿时间轴.py --fenjing 生成")

    dialogue = resolve(spec["对白音频"], ROOT)
    if not dialogue.is_file():
        sys.exit(f"对白音频不存在：{dialogue}")
    dialogue_peak = measure_peak(dialogue)

    warnings: list[str] = []

    def unit_start(number: int) -> float:
        row = units.get(number)
        if row is None:
            sys.exit(f"时间轴里没有单元 {number}")
        candidate = row.get("换乐候选")
        if candidate and candidate.get("吸附"):
            return candidate["时间"]
        return row["start"]

    # ---- BGM 段落 ----
    # 起点：`绝对时间`（精确钉情绪句）优先，否则 `起单元`（吸附字幕间隙）。
    # `"类型": "静默"`（或无曲目）保留为片尾静默收的表示；中段按M2连续铺乐。
    entries = []
    for item in spec["段落"]:
        if "绝对时间" in item:
            start = float(item["绝对时间"])
        else:
            start = unit_start(item["起单元"])
        is_silence = item.get("类型") == "静默" or not item.get("曲目")
        entries.append((start, item, is_silence))
    if entries and entries[0][0] > 0.2 and not entries[0][2]:
        warnings.append(
            f"第一段起点 {entries[0][0]:.2f}s（0 秒归开场定调锤音效，BGM 晚 2–4s 进是三段式骨架默认，无需处理）"
        )

    real_positions = [i for i, (_, _, sil) in enumerate(entries) if not sil]
    last_real = real_positions[-1] if real_positions else -1
    segments = []
    after_silence = False
    for position, (start, item, is_silence) in enumerate(entries):
        if is_silence:
            after_silence = True
            continue
        end = entries[position + 1][0] if position + 1 < len(entries) else duration
        if end - start < 15:
            warnings.append(f"「{item['label']}」只有 {end - start:.1f}s，短于 15s 的段会显碎")
        track = index.get(item["曲目"])
        if track is None:
            sys.exit(f"配乐库索引里没有：{item['曲目']}（跑一次 配乐库索引.py？）")
        category = item.get("类型", "对白")
        if category not in DELTA_BGM:
            sys.exit(f"未知段落类型「{category}」，可选：{'/'.join(DELTA_BGM)}/静默")

        measured = item.get("对白LUFS") or measure_window(dialogue, start, end - start)
        target = measured - DELTA_BGM[category]
        volume, clamped = gain_to_volume(target, track["响度LUFS"])
        if clamped:
            warnings.append(
                f"「{item['label']}」音量被夹到 {volume}：{Path(item['曲目']).name} 本身"
                f"{track['响度LUFS']:.1f} LUFS，够不到目标 {target:.1f} LUFS，换首曲子或改类型"
            )
        source_start = item.get("source_start", track["开头静音s"])
        usable = track["时长s"] - source_start
        if usable < end - start:
            warnings.append(
                f"「{item['label']}」需要 {end - start:.1f}s，但 {Path(item['曲目']).name} "
                f"从 {source_start:.1f}s 起只剩 {usable:.1f}s，会从头续铺（能听出重启）"
            )
        segments.append(
            {
                "label": item["label"],
                "audio": (LIBRARY / item["曲目"]).as_posix(),
                "start": round(start, 3),
                "end": round(end, 3),
                "source_start": round(source_start, 3),
                "volume": volume,
                # 静默后进场要精确砸在情绪句上，不淡入（对标实测直进）
                "fade_in": item.get("fade_in", 0.0 if (position == real_positions[0] or after_silence) else FADE_IN_DEFAULT),
                "fade_out": item.get("fade_out", FADE_OUT_LAST if position == last_real else FADE_OUT_DEFAULT),
                "_类型": category,
                "_对白LUFS": measured,
                "_目标LUFS": round(target, 1),
                "_曲目LUFS": track["响度LUFS"],
                "_静默后进": after_silence,
            }
        )
        after_silence = False

    # ---- 环境底噪层：按场景铺连续的声床，换场靠它抹平剪辑点 ----
    ambience = []
    for item in spec.get("环境", []):
        clip = index.get(item["文件"])
        if clip is None:
            sys.exit(f"配乐库索引里没有：{item['文件']}")
        # 环境床按单元原始边界严丝合缝地铺，不吸附字幕间隙——吸附会让相邻两床重叠零点几秒
        start_unit = units.get(item["起单元"])
        if start_unit is None:
            sys.exit(f"时间轴里没有单元 {item['起单元']}")
        start = start_unit["start"]
        stop_unit = units.get(item.get("止单元", item["起单元"]))
        if stop_unit is None:
            sys.exit(f"时间轴里没有单元 {item.get('止单元')}")
        end = min(stop_unit["end"], duration)
        measured = measure_window(dialogue, start, end - start)
        target = measured - DELTA_BGM["环境"]
        volume, clamped = gain_to_volume(target, clip["响度LUFS"])
        if clamped:
            warnings.append(
                f"环境「{item['label']}」音量被夹到 {volume}：{Path(item['文件']).name} 本身"
                f"{clip['响度LUFS']:.1f} LUFS，够不到目标 {target:.1f} LUFS"
            )
        ambience.append(
            {
                "label": item["label"],
                "audio": (LIBRARY / item["文件"]).as_posix(),
                "start": round(start, 3),
                "end": round(end, 3),
                "source_start": round(clip["开头静音s"], 3),
                "volume": volume,
                "fade_in": item.get("fade_in", 1.0),
                "fade_out": item.get("fade_out", 1.5),
                "_时长": round(end - start, 1),
                "_循环": max(1, round((end - start) / max(clip["时长s"], 0.1))),
            }
        )
    ambience.sort(key=lambda cue: cue["start"])
    for previous, current in zip(ambience, ambience[1:]):
        if current["start"] < previous["end"]:
            warnings.append(
                f"环境「{previous['label']}」与「{current['label']}」重叠——同一条轨放不下"
            )

    # ---- 音效 ----
    sfx = []
    for item in spec.get("音效", []):
        clip = index.get(item["文件"])
        if clip is None:
            sys.exit(f"配乐库索引里没有：{item['文件']}")
        category = item.get("类型", "爆点")
        if category not in DELTA_SFX:
            sys.exit(f"未知音效类型「{category}」，可选：{'/'.join(DELTA_SFX)}")
        at = item.get("绝对时间")
        if at is None:
            at = unit_start(item["单元"]) + float(item.get("偏移", 0.0))
        if category == "桥接":
            at -= SFX_BRIDGE_PREROLL
        at = round(max(0.0, at), 3)
        # 向下取整到毫秒：写盘脚本按微秒算，四舍五入会多出零点几毫秒的碎段
        length = math.floor(min(clip["时长s"] - clip["开头静音s"], duration - at) * 1000) / 1000
        if length <= 0:
            warnings.append(f"音效「{item['label']}」落在片尾之外，已跳过")
            continue
        volume, clamped = gain_to_volume(dialogue_peak + DELTA_SFX[category], clip["峰值dBFS"])
        if clamped:
            warnings.append(
                f"音效「{item['label']}」音量被夹到 {volume}：{Path(item['文件']).name} 峰值"
                f"{clip['峰值dBFS']:.1f} dBFS 偏低，砸不出该有的力度，换个更炸的素材"
            )
        sfx.append(
            {
                "label": item["label"],
                "audio": (LIBRARY / item["文件"]).as_posix(),
                "start": round(at, 3),
                "end": round(at + length, 3),
                "source_start": round(clip["开头静音s"], 3),
                "volume": volume,
                "_类型": category,
            }
        )
    # 碎音效必然互相重叠（一条轨放不下），按"第一条空着的轨"贪心铺开，自动分多轨。
    # 留 LANE_GUARD 的间隙：同轨两段哪怕只挨出零点几毫秒的重叠，剪映下次打开时会
    # **静默丢掉其中一段**（2026-07-27 22 集实测，四舍五入误差吃掉了 4 个音效）。
    sfx.sort(key=lambda cue: cue["start"])
    lanes: list[list[dict]] = []
    for cue in sfx:
        for lane in lanes:
            if lane[-1]["end"] + LANE_GUARD <= cue["start"]:
                lane.append(cue)
                break
        else:
            lanes.append([cue])
    for lane in lanes:                      # 落盘前按最终数值再验一次
        for previous, current in zip(lane, lane[1:]):
            if previous["end"] > current["start"]:
                sys.exit(
                    f"内部错误：同轨重叠未拆开——「{previous['label']}」{previous['end']}s "
                    f"压住「{current['label']}」{current['start']}s"
                )
    if len(lanes) > 6:
        warnings.append(
            f"音效需要 {len(lanes)} 条轨——剪辑面板会很拥挤，考虑合并同一时刻的多个点"
        )

    # ---- 自检 ----
    for previous, current in zip(segments, segments[1:]):
        gap = current["start"] - previous["end"]
        if gap < -0.001:
            warnings.append(f"「{previous['label']}」压住「{current['label']}」——同一条轨放不下")
        elif gap > 0.001 and not current.get("_静默后进"):
            warnings.append(
                f"「{previous['label']}」与「{current['label']}」之间有 {gap:.1f}s 缝但没声明静默段——"
                "中间对白应连续铺乐，请衔接前后段"
            )
    tail_silence = bool(entries) and entries[-1][2]
    if segments and abs(segments[-1]["end"] - duration) > 0.05 and not tail_silence:
        warnings.append(f"最后一段结束于 {segments[-1]['end']}s，草稿总长 {duration}s，对不上")

    return {
        "spec": spec,
        "duration": duration,
        "dialogue_peak": dialogue_peak,
        "bgm": segments,
        "ambience": ambience,
        "sfx": sfx,
        "sfx_lanes": lanes,
        "warnings": warnings,
    }


def write_outputs(spec_path: Path, built: dict) -> list[Path]:
    spec = built["spec"]
    out_dir = resolve(spec.get("输出目录", spec_path.parent.as_posix()), ROOT)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    def strip(cues):
        return [{k: v for k, v in cue.items() if not k.startswith("_")} for cue in cues]

    bgm_path = out_dir / "配乐方案.json"
    bgm_path.write_text(
        json.dumps(
            {
                "track_name": spec.get("bgm_track_name", "BGM-剧情分段"),
                "replace_audio_track_indexes": spec.get("replace_audio_track_indexes", []),
                "segments": strip(built["bgm"]),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    written.append(bgm_path)

    if built["ambience"]:
        amb_path = out_dir / "环境方案.json"
        amb_path.write_text(
            json.dumps(
                {"track_name": "AMB-环境底噪", "segments": strip(built["ambience"])},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        written.append(amb_path)

    for old in out_dir.glob("音效方案*.json"):
        old.unlink()
    prefix = spec.get("sfx_track_name", "SFX")
    for index, lane in enumerate(built["sfx_lanes"], start=1):
        sfx_path = out_dir / f"音效方案-{index}.json"
        sfx_path.write_text(
            json.dumps(
                {
                    "track_name": f"{prefix}-{index}",
                    "segments": strip(lane),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        written.append(sfx_path)

    lines = [
        "# 配乐方案对照表（自动生成，供审核）",
        "",
        f"草稿总长 {built['duration']:.3f}s ／ 对白峰值 {built['dialogue_peak']:.1f} dBFS",
        "",
        "## BGM",
        "",
        "| 段落 | 起 | 止 | 时长 | 类型 | 曲目 | 对白LUFS | 目标LUFS | 音量 |",
        "|---|---:|---:|---:|---|---|---:|---:|---:|",
    ]
    for cue in built["bgm"]:
        lines.append(
            f"| {cue['label']} | {cue['start']:.2f} | {cue['end']:.2f} | "
            f"{cue['end'] - cue['start']:.1f}s | {cue['_类型']} | {Path(cue['audio']).name} | "
            f"{cue['_对白LUFS']:.1f} | {cue['_目标LUFS']:.1f} | {cue['volume']} |"
        )
    if built["ambience"]:
        lines += [
            "",
            "## 环境底噪",
            "",
            "| 场景 | 起 | 止 | 时长 | 文件 | 循环次数 | 音量 |",
            "|---|---:|---:|---:|---|---:|---:|",
        ]
        for cue in built["ambience"]:
            lines.append(
                f"| {cue['label']} | {cue['start']:.2f} | {cue['end']:.2f} | {cue['_时长']}s | "
                f"{Path(cue['audio']).name} | {cue['_循环']} | {cue['volume']} |"
            )

    if built["sfx"]:
        lane_of = {
            id(cue): index
            for index, lane in enumerate(built["sfx_lanes"], start=1)
            for cue in lane
        }
        lines += [
            "",
            f"## 音效（{len(built['sfx'])} 个 ／ {len(built['sfx_lanes'])} 条轨）",
            "",
            "| 时间 | 轨 | 类型 | 音效 | 文件 | 音量 |",
            "|---:|---:|---|---|---|---:|",
        ]
        for cue in built["sfx"]:
            lines.append(
                f"| {cue['start']:.2f} | {lane_of[id(cue)]} | {cue['_类型']} | {cue['label']} | "
                f"{Path(cue['audio']).name} | {cue['volume']} |"
            )
    if built["warnings"]:
        lines += ["", "## 待处理提醒", ""] + [f"- {item}" for item in built["warnings"]]
    review_path = out_dir / "配乐方案.md"
    review_path.write_text("\n".join(lines), encoding="utf-8")
    written.append(review_path)
    return written


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="段落表 → 剪映配乐方案 JSON")
    parser.add_argument("spec_json")
    args = parser.parse_args()

    spec_path = Path(args.spec_json)
    built = build(spec_path)
    for path in write_outputs(spec_path, built):
        print(f"-> {path}")
    for warning in built["warnings"]:
        print(f"[提醒] {warning}")


if __name__ == "__main__":
    main()
