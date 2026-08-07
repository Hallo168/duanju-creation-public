from __future__ import annotations

import os
import socket
import sys

import uvicorn

from api import create_app

app = create_app()


def _resolve_bind() -> tuple[str, int]:
    """从环境变量解析监听地址，未指定时沿用默认 127.0.0.1:8000。"""
    host = (os.getenv("CHATGPT2API_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    raw_port = (os.getenv("CHATGPT2API_PORT") or "8000").strip() or "8000"
    try:
        port = int(raw_port)
    except ValueError:
        print(f"[启动终止] CHATGPT2API_PORT 不是合法端口: {raw_port!r}", file=sys.stderr)
        raise SystemExit(2)
    return host, port


def _ensure_single_backend(host: str, port: int) -> None:
    """确保同一 host:port 只允许一个后端实例。

    Windows 下普通 bind 可能与已存在的监听并存（端口可被重复绑定 / 劫持），
    导致两个后端各持一份内存态、面板号池对不上。这里用独占方式预绑定探测：
    只要目标端口已被占用就拒绝启动，把「同时跑两个后端」挡在写盘之前。
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Windows：独占占用，存在任何已有绑定都会让本次 bind 失败
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError:
        print(
            f"[启动终止] {host}:{port} 已被占用——很可能已经有一个后端在跑了。\n"
            f"  · 查占用：  netstat -ano | findstr :{port}\n"
            f"  · 停掉它：  taskkill /PID <上一步查到的PID> /F\n"
            f"  · 或换端口：设置环境变量 CHATGPT2API_PORT=<其它端口> 后再启动。",
            file=sys.stderr,
        )
        raise SystemExit(1)
    finally:
        probe.close()


if __name__ == "__main__":
    host, port = _resolve_bind()
    _ensure_single_backend(host, port)
    print(f"[chatgpt2api] backend listening on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, access_log=False, log_level="info")
