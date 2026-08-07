# -*- coding: utf-8 -*-
"""剪映草稿降级（解密）/ 升级（加密）/ 查状态。

剪映 10.7 会把 `draft_content.json` 与 `draft_meta_info.json` 加密保存
（首字节不是 `{`，同目录 `crypto_key_store.dat` 是密钥库）。密文本身不挡本项目的脚本
——工具/剪映/环境.py 里的读写函数明文密文都吃、并按原格式写回。
**只有需要用外部工具、或者人要直接看 JSON 时，才需要降级成明文。**

用法：
  python 工具/剪映/草稿降级.py --状态                    # 列出所有草稿的加密状态
  python 工具/剪映/草稿降级.py 22-都来了                  # 降级（解密）这一份
  python 工具/剪映/草稿降级.py 22-都来了 --升级            # 反向：重新加密回去
  python 工具/剪映/草稿降级.py --全部                     # 降级全部草稿（慎用）

降级前自动备份到 `草稿目录/.backup/降级前_时间戳/`。
目标草稿必须先关闭——剪映可能用内存里的旧内容覆盖外部写入。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from 环境 import (  # noqa: E402
    jy_crypto, 用UTF8输出, 草稿根目录, 草稿文件, 找草稿, 是加密草稿,
)

# 剪映会加密的两份 JSON
目标文件名 = ("draft_content.json", "draft_meta_info.json")


def 全部目标(draft_dir: Path) -> list[Path]:
    files = list(草稿文件(draft_dir))          # 根 + 主时间线的 draft_content.json
    meta = draft_dir / "draft_meta_info.json"
    if meta.exists():
        files.append(meta)
    return files


def 备份(draft_dir: Path, files: list[Path], 动作: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = draft_dir / ".backup" / f"{动作}前_{stamp}"
    for src in files:
        dst = backup_dir / src.relative_to(draft_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return backup_dir


def 处理(draft_dir: Path, *, 升级: bool) -> dict:
    动作 = "升级" if 升级 else "降级"
    files = 全部目标(draft_dir)
    backup_dir = 备份(draft_dir, files, 动作)

    结果 = []
    for path in files:
        raw = path.read_bytes()
        已加密 = jy_crypto.looks_encrypted(raw)
        if 升级 and 已加密:
            结果.append((path.name, "本来就是密文，跳过"))
            continue
        if not 升级 and not 已加密:
            结果.append((path.name, "本来就是明文，跳过"))
            continue
        if 升级:
            text = raw.decode("utf-8-sig", errors="replace")
            path.write_bytes(jy_crypto.encrypt(text).encode("latin-1"))
            结果.append((path.name, "明文 → 密文"))
        else:
            path.write_text(jy_crypto.decrypt(raw), encoding="utf-8")
            结果.append((path.name, "密文 → 明文"))
    return {"草稿": draft_dir.name, "动作": 动作, "备份": str(backup_dir), "文件": 结果}


def 状态表() -> None:
    root = 草稿根目录()
    print(f"草稿根目录：{root}\n")
    print(f"{'草稿':<28} {'格式':<6} 备注")
    print("-" * 60)
    for d in sorted(root.iterdir()):
        if not (d / "draft_content.json").exists():
            continue
        加密 = 是加密草稿(d)
        备注 = "" if not 加密 else "（本项目脚本可直接读写，不必降级）"
        print(f"{d.name:<28} {'密文' if 加密 else '明文':<6} {备注}")


def main() -> None:
    用UTF8输出()
    ap = argparse.ArgumentParser(description="剪映草稿降级（解密）/升级（加密）")
    ap.add_argument("草稿", nargs="?", help="草稿名或绝对路径")
    ap.add_argument("--状态", action="store_true", help="只列出所有草稿的加密状态")
    ap.add_argument("--全部", action="store_true", help="对根目录下所有草稿执行")
    ap.add_argument("--升级", action="store_true", help="反向操作：明文重新加密")
    args = ap.parse_args()

    if args.状态:
        状态表()
        return

    if args.全部:
        targets = [d for d in sorted(草稿根目录().iterdir()) if (d / "draft_content.json").exists()]
    elif args.草稿:
        targets = [找草稿(args.草稿)]
    else:
        ap.error("给一个草稿名，或用 --状态 / --全部")

    for draft_dir in targets:
        r = 处理(draft_dir, 升级=args.升级)
        print(f"\n== {r['草稿']}（{r['动作']}）")
        for name, how in r["文件"]:
            print(f"   {name:<24} {how}")
        print(f"   备份 → {r['备份']}")


if __name__ == "__main__":
    main()
