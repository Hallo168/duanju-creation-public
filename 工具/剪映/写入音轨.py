# -*- coding: utf-8 -*-
"""向现有剪映草稿写入按时间段编排的 BGM / 环境床 / 音效轨。

【本文件搬自 AutoTool 的 autotool/jianying/existing_draft_bgm.py】
2026-07-27 用户裁定：本项目要独立，不依赖 AutoTool 的代码。
路径与加解密统一走同目录的 环境.py（读 剪映配置.json）。

兼容剪映 10.x 的根 draft_content.json 与 Timelines/<主时间线>/draft_content.json。
写入前自动备份；加密草稿通过 jy_crypto.load_text 解密读取，**按原格式加密写回**。

加密回写是必须的（2026-07-27 21 集实测）：剪映 10.7 读不了明文的 draft_content.json，
写成明文的表现是草稿照常打开、视频字幕都在，但外部写入的音轨整条不出现。
是否加密按整个草稿判断（见 _draft_is_encrypted），不能只看 draft_content.json——
它一旦被写成明文，判断就会自己把自己带偏。

重跑幂等（2026-07-27）：剪映重新保存草稿时会把轨道重写成自己的精简结构，
自定义的 ``name`` 字段会被抹掉（只剩 id/type/is_default_name），因此"按同名轨替换"
在草稿被剪映打开保存过一次之后就失效，重跑会叠出第二条轨。
解法：写入时在草稿目录留一份旁证清单 ``.autotool_bgm/manifest.json``（剪映不会动它），
记录每条轨用到的音频路径与段数；下次重跑按"轨道名 或 旁证清单指纹"两条路一起认轨。
清单丢失时，可先用 ``--scan`` 列出现有音频轨，再用方案里的 ``replace_audio_track_indexes``
显式点名要替换的轨。

淡入淡出：段落可写 ``fade_in`` / ``fade_out``（秒）。字段结构取自剪映自身草稿
（materials.audio_fades: {id, type: "audio_fade", fade_in_duration, fade_out_duration}，
由段落的 extra_material_refs 引用）。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from 环境 import 音频时长, ffprobe路径, 用UTF8输出, 找草稿  # noqa: E402
from jy_crypto import encrypt, load_text, looks_encrypted  # noqa: E402


DEFAULT_TRACK_NAME = "BGM-剧情分段"
MANIFEST_RELATIVE = Path(".autotool_bgm") / "manifest.json"


def _uid() -> str:
    return uuid.uuid4().hex


def _seconds_to_us(value: Any) -> int:
    return int(round(float(value) * 1_000_000))


def _normalize_path(value: Any) -> str:
    """路径指纹：统一分隔符与大小写，供跨次运行比对（剪映会把 / 改写成 \\）。"""
    return str(value or "").replace("\\", "/").lower()


def _atomic_write_json(path: Path, data: dict, *, encrypted: bool) -> None:
    """按原文件的格式回写。

    加密草稿必须原样加密写回：剪映 10.7 读不了明文的 draft_content.json，
    表现是草稿能打开但外部写入的音轨整条消失（2026-07-27 21 集实测）。
    """
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    blob = encrypt(payload).encode("latin-1") if encrypted else payload.encode("utf-8")
    temp_path = path.with_name(f"{path.name}.autotool.tmp")
    temp_path.write_bytes(blob)
    os.replace(temp_path, path)


def _content_paths(draft_dir: Path) -> list[Path]:
    paths = []
    root_content = draft_dir / "draft_content.json"
    if root_content.exists():
        paths.append(root_content)

    project_path = draft_dir / "Timelines" / "project.json"
    if project_path.exists():
        project = json.loads(project_path.read_text(encoding="utf-8-sig"))
        timeline_id = project.get("main_timeline_id")
        timeline_content = draft_dir / "Timelines" / str(timeline_id) / "draft_content.json"
        if timeline_id and timeline_content.exists():
            paths.append(timeline_content)

    if not paths:
        raise FileNotFoundError(f"草稿中找不到 draft_content.json：{draft_dir}")
    return paths


def _backup_files(draft_dir: Path, content_paths: list[Path]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = draft_dir / ".backup" / f"autotool_bgm_{stamp}"
    for source in content_paths:
        relative = source.relative_to(draft_dir)
        destination = backup_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return backup_dir


def load_plan(plan_path: str | os.PathLike[str]) -> dict:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8-sig"))
    if not isinstance(plan.get("segments"), list) or not plan["segments"]:
        raise ValueError("配乐方案必须包含非空 segments 数组")
    return plan


def _material_index(document: dict) -> dict[str, dict]:
    return {
        item.get("id"): item
        for item in document.get("materials", {}).get("audios", [])
        if item.get("id")
    }


def _track_fingerprint(track: dict, audios: dict[str, dict]) -> dict:
    paths = sorted(
        {
            _normalize_path(audios.get(segment.get("material_id"), {}).get("path"))
            for segment in track.get("segments", [])
        }
    )
    return {"audio_paths": [p for p in paths if p], "segment_count": len(track.get("segments", []))}


def scan_audio_tracks(draft_dir: str | os.PathLike[str]) -> dict:
    """只读：列出草稿现有音频轨，供写入前核对是否已有旧配乐轨。"""
    draft_path = Path(draft_dir).resolve()
    content_paths = _content_paths(draft_path)
    document = json.loads(load_text(content_paths[0]))
    audios = _material_index(document)

    tracks = []
    for index, track in enumerate(
        [item for item in document.get("tracks", []) if item.get("type") == "audio"]
    ):
        segments = track.get("segments", [])
        starts = [segment.get("target_timerange", {}).get("start", 0) for segment in segments]
        ends = [
            segment.get("target_timerange", {}).get("start", 0)
            + segment.get("target_timerange", {}).get("duration", 0)
            for segment in segments
        ]
        tracks.append(
            {
                "audio_track_index": index,
                "name": track.get("name"),
                "is_default_name": track.get("is_default_name"),
                "segment_count": len(segments),
                "span_s": [min(starts, default=0) / 1e6, max(ends, default=0) / 1e6],
                "materials": sorted(
                    {
                        os.path.basename(str(audios.get(segment.get("material_id"), {}).get("path", "")))
                        for segment in segments
                    }
                ),
            }
        )
    return {
        "draft_dir": str(draft_path),
        "draft_duration_s": int(document.get("duration") or 0) / 1e6,
        "audio_track_count": len(tracks),
        "audio_tracks": tracks,
        "manifest": _load_manifest(draft_path),
    }


def _draft_is_encrypted(draft_dir: Path, content_paths: list[Path]) -> bool:
    """草稿是不是加密草稿。

    只看 draft_content.json 不够：一旦被外部写成明文，这个判断就自己把自己带偏了。
    draft_meta_info.json 同属剪映加密，任何一份是密文就按加密草稿回写。
    用户按 字幕对齐规范 C8 整份解密时两份都是明文，那就照明文写。
    """
    probes = [*content_paths, draft_dir / "draft_meta_info.json"]
    return any(path.exists() and looks_encrypted(path.read_bytes()) for path in probes)


def _load_manifest(draft_dir: Path) -> dict:
    manifest_path = draft_dir / MANIFEST_RELATIVE
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_manifest(draft_dir: Path, manifest: dict) -> None:
    manifest_path = draft_dir / MANIFEST_RELATIVE
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _prepare_plan(plan: dict, draft_duration_us: int) -> list[dict]:
    prepared = []
    for index, raw in enumerate(plan["segments"], start=1):
        audio_path = Path(raw["audio"]).expanduser().resolve()
        if not audio_path.is_file():
            raise FileNotFoundError(f"第 {index} 段配乐不存在：{audio_path}")

        start = _seconds_to_us(raw["start"])
        end = _seconds_to_us(raw["end"])
        source_start = _seconds_to_us(raw.get("source_start", 0))
        volume = max(0.0, min(1.0, float(raw.get("volume", 0.14))))
        fade_in = _seconds_to_us(raw.get("fade_in", 0))
        fade_out = _seconds_to_us(raw.get("fade_out", 0))
        if start < 0 or end <= start:
            raise ValueError(f"第 {index} 段时间无效：start={start}, end={end}")
        if end > draft_duration_us:
            raise ValueError(
                f"第 {index} 段超出草稿时长：{end / 1e6:.3f}s > "
                f"{draft_duration_us / 1e6:.3f}s"
            )
        if fade_in < 0 or fade_out < 0:
            raise ValueError(f"第 {index} 段淡化时长不能为负")
        if fade_in + fade_out > end - start:
            raise ValueError(
                f"第 {index} 段淡入+淡出({(fade_in + fade_out) / 1e6:.2f}s)超过段长"
                f"({(end - start) / 1e6:.2f}s)"
            )

        audio_duration = _seconds_to_us(音频时长(audio_path))
        if source_start < 0 or source_start >= audio_duration:
            raise ValueError(
                f"第 {index} 段 source_start 超出音频时长：{audio_path.name}"
            )
        prepared.append(
            {
                "audio": str(audio_path),
                "audio_duration": audio_duration,
                "start": start,
                "end": end,
                "source_start": source_start,
                "volume": volume,
                "fade_in": fade_in,
                "fade_out": fade_out,
                "label": str(raw.get("label", f"段{index}")),
            }
        )
    return prepared


def _check_overlaps(prepared: list[dict]) -> None:
    ordered = sorted(prepared, key=lambda cue: cue["start"])
    for previous, current in zip(ordered, ordered[1:]):
        if current["start"] < previous["end"]:
            raise ValueError(
                f"段落时间重叠（同一条轨不允许）：「{previous['label']}」"
                f"{previous['start'] / 1e6:.3f}–{previous['end'] / 1e6:.3f}s 与"
                f"「{current['label']}」{current['start'] / 1e6:.3f}–{current['end'] / 1e6:.3f}s"
            )


def _build_track(
    prepared: list[dict], track_name: str
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    audio_materials: list[dict] = []
    speed_materials: list[dict] = []
    fade_materials: list[dict] = []
    segments: list[dict] = []
    audio_ids: dict[str, str] = {}

    for cue in prepared:
        audio_path = cue["audio"]
        audio_id = audio_ids.get(audio_path)
        if audio_id is None:
            audio_id = _uid()
            audio_ids[audio_path] = audio_id
            audio_materials.append(
                {
                    "app_id": 0,
                    "category_id": "",
                    "category_name": "local",
                    "check_flag": 3,
                    "copyright_limit_type": "none",
                    "duration": cue["audio_duration"],
                    "effect_id": "",
                    "formula_id": "",
                    "id": audio_id,
                    "local_material_id": audio_id,
                    "music_id": audio_id,
                    "name": Path(audio_path).name,
                    "path": audio_path,
                    "source_platform": 0,
                    "type": "extract_music",
                    "wave_points": [],
                }
            )

        tiles: list[dict] = []
        timeline_cursor = cue["start"]
        source_cursor = cue["source_start"]
        while timeline_cursor < cue["end"]:
            available = cue["audio_duration"] - source_cursor
            duration = min(cue["end"] - timeline_cursor, available)
            if duration < 1000:
                # 上游把秒数四舍五入到毫秒，可能多出零点几毫秒——那不是一段音乐，是碎屑
                break
            speed_id = _uid()
            speed_materials.append(
                {
                    "id": speed_id,
                    "type": "speed",
                    "mode": 0,
                    "speed": 1.0,
                    "curve_speed": None,
                }
            )
            tile = {
                "id": _uid(),
                "material_id": audio_id,
                "target_timerange": {
                    "start": timeline_cursor,
                    "duration": duration,
                },
                "source_timerange": {
                    "start": source_cursor,
                    "duration": duration,
                },
                "speed": 1.0,
                "volume": cue["volume"],
                "extra_material_refs": [speed_id],
                "common_keyframes": [],
                "keyframe_refs": [],
                "enable_adjust": True,
                "last_nonzero_volume": cue["volume"],
                "reverse": False,
                "visible": True,
            }
            tiles.append(tile)
            segments.append(tile)
            timeline_cursor += duration
            source_cursor = 0

        # 音频不够长时一段配乐会被续铺成多块：淡入只给第一块、淡出只给最后一块
        if tiles and (cue["fade_in"] or cue["fade_out"]):
            for tile, fade_in, fade_out in (
                (tiles[0], cue["fade_in"], 0),
                (tiles[-1], 0, cue["fade_out"]),
            ):
                if not (fade_in or fade_out):
                    continue
                existing = next(
                    (
                        item
                        for item in fade_materials
                        if item["id"] in tile["extra_material_refs"]
                    ),
                    None,
                )
                if existing is not None:
                    existing["fade_in_duration"] = existing["fade_in_duration"] or fade_in
                    existing["fade_out_duration"] = existing["fade_out_duration"] or fade_out
                    continue
                fade_id = _uid()
                fade_materials.append(
                    {
                        "id": fade_id,
                        "type": "audio_fade",
                        "fade_in_duration": fade_in,
                        "fade_out_duration": fade_out,
                    }
                )
                tile["extra_material_refs"].append(fade_id)

    track = {
        "id": _uid(),
        "type": "audio",
        "attribute": 0,
        "flag": 0,
        "is_default_name": False,
        "name": track_name,
        "segments": segments,
    }
    return track, audio_materials, speed_materials, fade_materials


def _match_by_fingerprint(track: dict, audios: dict[str, dict], recorded: dict) -> bool:
    """旁证清单认轨：段数相同且用到的音频文件集合完全一致，才算同一条轨。"""
    if not recorded:
        return False
    fingerprint = _track_fingerprint(track, audios)
    return (
        fingerprint["segment_count"] == recorded.get("segment_count")
        and fingerprint["audio_paths"] == sorted(recorded.get("audio_paths", []))
        and bool(fingerprint["audio_paths"])
    )


def _remove_tracks(
    data: dict,
    track_name: str,
    recorded: dict,
    explicit_indexes: list[int],
    extra_names: list[str] | None = None,
) -> list[dict]:
    """删除旧配乐轨（按轨道名 / 旁证清单指纹 / 额外点名的轨道名 / 音频轨序号）。

    序号是**位置**，同一批里前一次写入会改变轨道顺序，拿旧扫描的序号会误删——
    能用名字就别用序号（2026-07-27 实测：用陈旧序号把刚写好的 BGM 轨删掉了）。
    """
    wanted_names = {track_name, *(extra_names or [])}
    tracks = data.setdefault("tracks", [])
    audios = _material_index(data)
    audio_track_positions = [
        position for position, track in enumerate(tracks) if track.get("type") == "audio"
    ]

    doomed_positions = set()
    for order, position in enumerate(audio_track_positions):
        if order in explicit_indexes:
            doomed_positions.add(position)
    for position, track in enumerate(tracks):
        if track.get("name") in wanted_names:
            doomed_positions.add(position)
        elif track.get("type") == "audio" and _match_by_fingerprint(track, audios, recorded):
            doomed_positions.add(position)

    if not doomed_positions:
        return []

    removed = [tracks[position] for position in sorted(doomed_positions)]
    audio_ids = {
        segment.get("material_id")
        for track in removed
        for segment in track.get("segments", [])
        if segment.get("material_id")
    }
    extra_ids = {
        material_id
        for track in removed
        for segment in track.get("segments", [])
        for material_id in segment.get("extra_material_refs", [])
    }
    data["tracks"] = [
        track for position, track in enumerate(tracks) if position not in doomed_positions
    ]

    # 同一份音频素材可能被别的轨（如用户手动铺的音效）共用，只清理没人再引用的
    still_used_audio = {
        segment.get("material_id")
        for track in data["tracks"]
        for segment in track.get("segments", [])
    }
    still_used_extra = {
        material_id
        for track in data["tracks"]
        for segment in track.get("segments", [])
        for material_id in segment.get("extra_material_refs", [])
    }
    materials = data.setdefault("materials", {})
    materials["audios"] = [
        item
        for item in materials.get("audios", [])
        if item.get("id") not in audio_ids or item.get("id") in still_used_audio
    ]
    for key in ("speeds", "audio_fades"):
        materials[key] = [
            item
            for item in materials.get(key, [])
            if item.get("id") not in extra_ids or item.get("id") in still_used_extra
        ]
    return removed


def apply_plan(
    draft_dir: str | os.PathLike[str],
    plan: dict,
    *,
    dry_run: bool = False,
) -> dict:
    draft_path = Path(draft_dir).resolve()
    content_paths = _content_paths(draft_path)
    draft_encrypted = _draft_is_encrypted(draft_path, content_paths)
    documents = [json.loads(load_text(path)) for path in content_paths]
    draft_duration = int(documents[0].get("duration") or 0)
    if draft_duration <= 0:
        raise ValueError("草稿 duration 缺失或无效")

    track_name = str(plan.get("track_name") or DEFAULT_TRACK_NAME)
    explicit_indexes = [int(item) for item in plan.get("replace_audio_track_indexes", [])]
    extra_names = [str(item) for item in plan.get("replace_track_names", [])]
    prepared = _prepare_plan(plan, draft_duration)
    _check_overlaps(prepared)
    track, audio_materials, speed_materials, fade_materials = _build_track(prepared, track_name)

    manifest = _load_manifest(draft_path)
    recorded = manifest.get(track_name, {})
    audios_preview = _material_index(documents[0])
    would_remove = [
        {
            "name": item.get("name"),
            "segment_count": len(item.get("segments", [])),
            "materials": sorted(
                {
                    os.path.basename(
                        str(audios_preview.get(segment.get("material_id"), {}).get("path", ""))
                    )
                    for segment in item.get("segments", [])
                }
            ),
        }
        for item in _remove_tracks(
            copy.deepcopy(documents[0]), track_name, recorded, explicit_indexes, extra_names
        )
    ]

    if dry_run:
        return {
            "dry_run": True,
            "draft_duration_s": draft_duration / 1e6,
            "content_files": [str(path) for path in content_paths],
            "track_name": track_name,
            "cue_count": len(prepared),
            "segment_count": len(track["segments"]),
            "fade_count": len(fade_materials),
            "will_replace_tracks": would_remove,
            "encrypted_draft": draft_encrypted,
            "existing_audio_track_count": sum(
                1 for item in documents[0].get("tracks", []) if item.get("type") == "audio"
            ),
        }

    backup_dir = _backup_files(draft_path, content_paths)
    (backup_dir / "applied_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    removed_count = 0
    for path, document in zip(content_paths, documents):
        removed_count += len(
            _remove_tracks(document, track_name, recorded, explicit_indexes, extra_names)
        )
        materials = document.setdefault("materials", {})
        materials.setdefault("audios", []).extend(copy.deepcopy(audio_materials))
        materials.setdefault("speeds", []).extend(copy.deepcopy(speed_materials))
        if fade_materials:
            materials.setdefault("audio_fades", []).extend(copy.deepcopy(fade_materials))
        document.setdefault("tracks", []).append(copy.deepcopy(track))
        _atomic_write_json(path, document, encrypted=draft_encrypted)

    manifest[track_name] = {
        "audio_paths": sorted({_normalize_path(cue["audio"]) for cue in prepared}),
        "segment_count": len(track["segments"]),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_manifest(draft_path, manifest)

    return {
        "dry_run": False,
        "draft_duration_s": draft_duration / 1e6,
        "content_files": [str(path) for path in content_paths],
        "backup_dir": str(backup_dir),
        "track_name": track_name,
        "cue_count": len(prepared),
        "segment_count": len(track["segments"]),
        "fade_count": len(fade_materials),
        "replaced_track_copies": removed_count,
        "replaced_tracks": would_remove,
        "encrypted_draft": draft_encrypted,
        "audio_files": sorted({cue["audio"] for cue in prepared}),
    }


# ============================================================
# 命令行入口
# ============================================================
def main() -> None:
    用UTF8输出()
    parser = argparse.ArgumentParser(description="向现有剪映草稿写入 BGM / 环境床 / 音效轨")
    parser.add_argument("草稿", help="草稿名（在配置的草稿根目录下找）或绝对路径")
    parser.add_argument("方案", nargs="?", help="方案 JSON；--scan 时可省略")
    parser.add_argument("--scan", action="store_true", help="只列出现有音频轨，不写盘")
    parser.add_argument("--dry-run", action="store_true", help="只校验，不写盘")
    args = parser.parse_args()

    draft_dir = 找草稿(args.草稿)
    if args.scan:
        print(json.dumps(scan_audio_tracks(draft_dir), ensure_ascii=False, indent=2))
        return
    if not args.方案:
        parser.error("除 --scan 外必须给出方案 JSON")

    result = apply_plan(draft_dir, load_plan(args.方案), dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
