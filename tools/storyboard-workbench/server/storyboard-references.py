"""Read-only adapter for the same ordered image resolution used by video jobs."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "工具"))
from video_reference_utils import mount_tokens, resolve_asset
from canvas_video_batch import clean_mount_token


def describe(task):
    story = Path(task["story"])
    units = []
    for unit in task["units"]:
        items = []
        error = ""
        try:
            tokens = mount_tokens(unit["section"], unit["number"])
        except (ValueError, FileNotFoundError) as exc:
            tokens = []
            error = str(exc)
        for index, token in enumerate(tokens, 1):
            reference = clean_mount_token(token)
            asset_id = re.search(r"\b([RSP]\d+[A-Z]?)\b", reference)
            label = re.sub(r"[（(][^（）()]*\.(?:png|jpe?g|webp)[）)]", "", reference, flags=re.I).strip()
            item = {
                "number": index,
                "reference": reference,
                "assetId": asset_id.group(1) if asset_id else "",
                "name": re.sub(r"^[RSP]\d+[A-Z]?\s*[-·｜|]?\s*", "", label),
                "detail": "",
                "filename": None,
                "status": "missing",
                "message": "",
            }
            declared = re.match(r"第(\d+)个上传", token)
            if declared and int(declared.group(1)) != index:
                item.update(status="ambiguous", message=f"挂载标号为第{declared.group(1)}个，实际位于图片{index}，请核对上传顺序。")
                items.append(item)
                continue
            try:
                file = resolve_asset(story, token, unit["number"])
                explicit = re.search(r"(?:^|[（(])([^（）()]+\.(?:png|jpe?g|webp))[）)]?\s*$", reference, flags=re.I)
                if explicit and not re.search(r"\[[^\]]+\]\(", reference):
                    expected = Path(explicit.group(1)).name
                    expected = re.sub(r"^[RSP]\d+[A-Z]?\s+(?=[RSP]\d+[A-Z]?-)", "", expected)
                    if file.name.casefold() != expected.casefold():
                        raise FileNotFoundError(expected)
                if not file.is_file() or not file.stat().st_size:
                    raise FileNotFoundError(file)
                parts = file.stem.split("-")
                if len(parts) > 1 and re.fullmatch(r"[RSP]\d+[A-Z]?", parts[0]):
                    item["name"] = parts[1]
                    item["detail"] = " · ".join(parts[2:])
                item.update(path=str(file.resolve()), filename=file.name, status="ready")
            except (ValueError, FileNotFoundError, OSError) as exc:
                item["status"] = "ambiguous" if "多个" in str(exc) else "missing"
                item["message"] = "匹配到多张图片，请明确文件名或版本。" if item["status"] == "ambiguous" else "未找到可用图片，请核对挂载文件。"
            items.append(item)
        units.append({"number": unit["number"], "items": items, "error": error})
    return {"units": units}


if __name__ == "__main__":
    print(json.dumps(describe(json.load(sys.stdin)), ensure_ascii=False))
