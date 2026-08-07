# -*- coding: utf-8 -*-
"""外部依赖一键安装：重建三个 vendor 项目的运行环境（node_modules / .venv）。

新机器克隆主仓库后运行：  python 外部依赖/安装.py
前置：Node.js（含 npm）、uv。凭据文件（chatgpt2api/config.json、flow2api/config/setting.toml）
不在仓库里，装完需按 README.md 自行配置。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(desc: str, cmd: list[str], cwd: Path) -> bool:
    print(f"\n=== {desc} ===\n    {' '.join(cmd)}  (cwd={cwd})")
    try:
        subprocess.run(cmd, cwd=str(cwd), check=True, shell=(cmd[0] in {"npm", "npm.cmd"}))
        print(f"[OK] {desc}")
        return True
    except FileNotFoundError:
        print(f"[缺工具] {desc}: 找不到 {cmd[0]}，请先安装（Node.js / uv）")
    except subprocess.CalledProcessError as exc:
        print(f"[失败] {desc}: 退出码 {exc.returncode}")
    return False


def main() -> int:
    results = []

    opencli = ROOT / "OpenCLI-Symphony"
    if (opencli / "node_modules").exists():
        print("[跳过] OpenCLI node_modules 已存在")
        results.append(True)
    else:
        results.append(run("OpenCLI-Symphony: npm ci", ["npm", "ci"], opencli))

    c2a = ROOT / "chatgpt2api"
    results.append(run("chatgpt2api: uv sync", ["uv", "sync"], c2a))

    f2a = ROOT / "flow2api"
    venv_py = f2a / ".venv" / "Scripts" / "python.exe"
    if not venv_py.exists():
        results.append(run("flow2api: uv venv", ["uv", "venv", ".venv"], f2a))
    results.append(
        run(
            "flow2api: uv pip install -r requirements.txt",
            ["uv", "pip", "install", "-r", "requirements.txt", "--python", str(venv_py)],
            f2a,
        )
    )

    print("\n" + ("全部完成" if all(results) else "部分步骤失败，见上方输出"))
    print("别忘了补凭据：chatgpt2api/config.json 与 flow2api/config/setting.toml（见 README.md）")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
