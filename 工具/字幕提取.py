# -*- coding: utf-8 -*-
"""剪映草稿字幕提取：把字幕轨导出为 tsv（序号\t开始μs\t时长μs\t文本），供对照台词稿校对。

用法：
  python 工具/字幕提取.py "<草稿名或绝对路径>" "<输出tsv>"
示例：
  python 工具/字幕提取.py "22-都来了" "工具/运行垃圾/tasks/22/字幕导出.tsv"

- 只导出 type=="subtitle" 的素材（语音识别字幕）；压屏字幕/标题/贴纸不在内
- 顺序 = 全部文本轨合并后按 target_timerange.start 升序（与 字幕回写.py 的对位顺序一致）
- 时间单位是微秒（μs）
- **明文/加密草稿都能读**（2026-07-27 项目独立化后走 工具/剪映/环境.py，不必再手动降级）
"""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "剪映"))
from 环境 import 找草稿, 读草稿JSON, 草稿文件, 用UTF8输出  # noqa: E402


def 取字幕段(data: dict) -> list[dict]:
    """全部文本轨合并后按起点升序；只要 ASR 字幕。提取与回写共用这个顺序。"""
    texts = {t["id"]: t for t in data.get("materials", {}).get("texts", [])}
    segs = [
        s
        for tr in data.get("tracks", [])
        if tr.get("type") == "text"
        for s in tr.get("segments", [])
        if texts.get(s.get("material_id"), {}).get("type") == "subtitle"
        if isinstance(s.get("target_timerange"), dict)
    ]
    # 剪映的 0 秒字幕可能省略 start 字段；与草稿时间轴脚本保持一致，按 0 处理。
    segs.sort(key=lambda s: s["target_timerange"].get("start", 0))
    return segs


def main():
    用UTF8输出()
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    draft_dir = 找草稿(sys.argv[1])
    out_path = sys.argv[2]

    # 剪映 10.x 新草稿的根 draft_content.json 可能只保存工程壳，
    # 字幕与轨道仅存在 Timelines/<main_timeline_id>/draft_content.json。
    # 在两份候选中选择真正含字幕素材的一份，避免固定读根文件。
    candidates = [读草稿JSON(path) for path in 草稿文件(draft_dir)]
    data = next(
        (item for item in candidates if item.get("materials", {}).get("texts")),
        candidates[0],
    )
    texts = {t["id"]: t for t in data.get("materials", {}).get("texts", [])}
    segs = 取字幕段(data)

    with io.open(out_path, "w", encoding="utf-8") as out:
        for i, s in enumerate(segs):
            tm = texts[s["material_id"]]
            try:
                txt = json.loads(tm["content"]).get("text", "")
            except Exception:
                txt = tm.get("recognize_text", "")
            tr = s["target_timerange"]
            out.write("%d\t%d\t%d\t%s\n" % (i, tr.get("start", 0), tr["duration"], txt))
    print("字幕 %d 条 -> %s" % (len(segs), out_path))


if __name__ == "__main__":
    main()
