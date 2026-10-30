# -*- coding: utf-8 -*-
"""剪映草稿字幕回写：按修正后的 tsv 覆写字幕文本。

用法：
  python 工具/字幕回写.py "<草稿名或绝对路径>" "<修正tsv>"
修正 tsv 格式：每行「序号\t文本」（也兼容 字幕提取.py 的四列格式，取最后一列为文本）

硬性前置：
- 剪映必须已关闭该草稿（软件开着会用内存里的旧内容覆盖本次修改）
- 修正条数必须与草稿字幕条数一致（对不上直接报错退出，不写盘）

行为（2026-07-27 项目独立化后）：
- **明文/加密草稿都能写**，并按原格式回写（加密草稿仍写密文，见 后期规范 M14.1）
- **根目录与 Timelines 主时间线两份 draft_content.json 都改**——以前只改根目录那份，
  剪映 10.x 两边都存字幕，漏改会让改动在重开草稿后被另一份覆盖回去
- 备份到 `草稿目录/.backup/字幕回写前_时间戳/`（保留每一次，不再互相覆盖）
- 同步改 content 与 base_content 的 text，styles[].range 同步为 [0, 新文本长度]
- recognize_text（识别原始记录）保留不动
- 默认沿用 工具/剪映/模板/字幕样式-156.json 的显示参数，仅作用于对白字幕
"""
import copy
import io
import json
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "剪映"))
from 环境 import (  # noqa: E402
    找草稿, 读草稿JSON, 写草稿JSON, 草稿文件, 是加密草稿, 用UTF8输出, 展开模板资源,
)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from 字幕提取 import 取字幕段  # noqa: E402


def 读修正(fix_path: str) -> dict[int, str]:
    corrected = {}
    with io.open(fix_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            corrected[int(parts[0])] = parts[-1]
    return corrected


def 套字幕样式(material: dict, segment: dict, template: dict) -> None:
    material.update(copy.deepcopy(template["material_style"]))
    material["fonts"] = [dict(copy.deepcopy(font), id=str(uuid.uuid4()).upper()) for font in template["fonts"]]
    for key in ("content", "base_content"):
        if not material.get(key):
            continue
        content = json.loads(material[key])
        text = content.get("text", "")
        content.update(copy.deepcopy(template["content_style"]))
        for style in content.get("styles", []):
            style["range"] = [0, len(text)]
        material[key] = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    segment.update(copy.deepcopy(template["segment_style"]))


def 改一份(data: dict, corrected: dict[int, str], style_template: dict | None = None) -> int:
    # 新版剪映的根工程文件可能不含 texts，字幕只在主时间线文件中。
    # 这种工程壳无需修改，也不能拿它做条数校验。
    raw_texts = data.get("materials", {}).get("texts", [])
    if not raw_texts:
        return 0
    texts = {t["id"]: t for t in raw_texts}
    segs = 取字幕段(data)
    if len(corrected) != len(segs):
        sys.exit("修正条数 %d != 草稿字幕条数 %d，不写盘" % (len(corrected), len(segs)))

    changed = 0
    for i, s in enumerate(segs):
        tm = texts[s["material_id"]]
        new_text = corrected[i]
        if style_template is not None:
            套字幕样式(tm, s, style_template)
        try:
            old_text = json.loads(tm["content"]).get("text", "")
        except Exception:
            old_text = None
        if old_text == new_text:
            continue
        for key in ("content", "base_content"):
            raw = tm.get(key)
            if not raw:
                continue
            try:
                c = json.loads(raw)
            except Exception:
                continue
            for st in c.get("styles", []):
                rng = st.get("range")
                if rng and len(rng) == 2:
                    st["range"] = [0, len(new_text)]
            c["text"] = new_text
            tm[key] = json.dumps(c, ensure_ascii=False, separators=(",", ":"))
        changed += 1
    return changed


def main():
    用UTF8输出()
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    draft_dir = 找草稿(sys.argv[1])
    corrected = 读修正(sys.argv[2])

    template_path = Path(__file__).resolve().parent / "剪映" / "模板" / "字幕样式-156.json"
    style_template = 展开模板资源(json.loads(template_path.read_text(encoding="utf-8"))) if template_path.exists() else None

    files = 草稿文件(draft_dir)
    加密 = 是加密草稿(draft_dir)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = draft_dir / ".backup" / f"字幕回写前_{stamp}"
    for src in files:
        dst = backup_dir / src.relative_to(draft_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    总数 = 0
    for path in files:
        data = 读草稿JSON(path)
        changed = 改一份(data, corrected, style_template)
        写草稿JSON(path, data, 加密=加密)
        读草稿JSON(path)          # 回读验证：能解析才算写成功
        总数 = max(总数, changed)
        print("  %-12s 改了 %d 条" % (path.parent.name if path.parent != draft_dir else "根目录", changed))

    print("字幕回写完成：%d 条改动 ／ %d 份文件 ／ 格式=%s" % (总数, len(files), "密文" if 加密 else "明文"))
    if style_template is not None:
        print("字幕样式已沿用156模板：烟波宋／13号／白字黑描边／Y=-0.56")
    print("备份 → %s" % backup_dir)


if __name__ == "__main__":
    main()
