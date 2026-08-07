#!/usr/bin/env python3
"""Submit and collect TikTok Symphony (Dreamina Seedance 2.0) video units.

通过本机代理 工具/symphony/（先启动 启动代理.cmd）走 Symphony 直连。相对其他通道的
两个差别：①参考图直接用本地绝对路径，不需要图床 ②计价是预付费积分池（1秒=1积分），
所以提交前用 --confirm-credits 做一道预算护栏。

    # 0. 起代理（另开一个窗口，或双击 工具/symphony/启动代理.cmd）
    # 1. 只预检不花钱：核对模型表/许可证/登录态/挂载/时长/总积分
    python 工具/symphony_video_batch.py preflight --story-root stories/29-xxx \
        --prompt-file 分镜-15秒/分镜提示词.md --units 1-12

    # 2. 用户放行后提交（--confirm-credits 必须等于预检报出的总积分，否则拒绝）
    #    加 --watch 则提交完自动进入轮询下载，一条命令无人值守跑完整批
    python 工具/symphony_video_batch.py submit --story-root stories/29-xxx \
        --prompt-file 分镜-15秒/分镜提示词.md --units 1 \
        --model "Dreamina Seedance 2.0" --confirm-credits 15 \
        --ledger 工具/运行垃圾/tasks/29/symphony-29-U1.jsonl --watch

    # 3. 轮询并下载（可反复跑，已下好的单元会跳过；--watch 已含这步）
    python 工具/symphony_video_batch.py poll --story-root stories/29-xxx \
        --prompt-file 分镜-15秒/分镜提示词.md \
        --ledger 工具/运行垃圾/tasks/29/symphony-29-U1.jsonl

整集大批量要摊到全部主账号时，preflight 与 submit 都加 --main-accounts all（省略
--accounts 即等同 all）：池子是「全部主账号 × 各自子账号」的并集，积分与 5 条并发额度
按每个子账号独立求和。poll 不需要这个参数——账本逐单元记了 main_account 与 account，
轮询按单元各自路由。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 允许从任意 cwd 复用同目录的公共解析函数

from canvas_video_batch import (
    append_ledger,
    load_ledger,
    mount_line,
    mount_tokens,
    parse_units,
    resolve_mount,
    unit_section,
    video_prompt,
)

SOURCE_DIR = Path(__file__).resolve().parent
SYMPHONY_DIR = Path(os.environ.get("SYMPHONY_SOURCE_DIR", SOURCE_DIR / "symphony"))
CONFIG_PATH = Path(os.environ.get("SYMPHONY_BATCH_CONFIG", SOURCE_DIR / "api配置.json"))
LOCAL_CONFIG_PATH = Path(
    os.environ.get("SYMPHONY_LOCAL_CONFIG", SYMPHONY_DIR / "symphony配置.json")
)
ASSET_CACHE_PATH = Path(
    os.environ.get("SYMPHONY_ASSET_CACHE", SYMPHONY_DIR / "data" / "asset_cache.json")
)
LIMIT_MARKS_PATH = Path(
    os.environ.get("SYMPHONY_ACCOUNT_LIMITS", SYMPHONY_DIR / "data" / "账号限制.json")
)
PROVIDER = "symphony_video"
PLATFORM = "symphony"
DEFAULT_BASE_URL = "http://127.0.0.1:8765"
ALLOWED_DURATIONS = (5, 10, 12, 15)
MAX_REFS = 9
MAX_INFLIGHT = 5  # 单子账号同时渲染上限（平台侧硬限制）
QUOTA_RESET_HOUR = 8  # 账号日额度每天早上 8 点刷新（2026-08-01 用户裁定）


# ---------------------------------------------------------------- 配置与请求


def load_config() -> tuple[str, dict]:
    """base_url 以 api配置.json 为准；本机路径（ffprobe/默认账号）读 symphony配置.json。"""
    try:
        provider = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")).get("providers", {}).get(PROVIDER, {})
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取统一 API 配置 {CONFIG_PATH}: {exc}") from exc
    base_url = str(provider.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    local: dict = {}
    if LOCAL_CONFIG_PATH.exists():
        try:
            local = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"无法读取本机配置 {LOCAL_CONFIG_PATH}: {exc}") from exc
    return base_url, local


def disabled_main_accounts(local: dict) -> set[str]:
    """symphony配置.json「停用主账号」：封号/停用的主账号，提交、热预存、轮询全流程不再触碰。"""
    return {str(name) for name in (local.get("停用主账号") or []) if name}


def api_json(url: str, method: str = "GET", payload: dict | None = None, timeout: int = 180) -> dict:
    """代理是本机回环、不需要鉴权。GET 幂等可重试；POST 一律单次——提交会扣积分。"""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    attempts = 3 if method == "GET" else 1
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            extra = None
            try:
                error = json.loads(detail).get("error", {})
                code = str(error.get("code") or "")
                message = str(error.get("message") or "")
                # details 里带 CLI 的 exit_code 与 stderr，没它没法诊断 OpenCLI 侧的失败
                extra = error.get("details")
                if extra:
                    message += f" | details={json.dumps(extra, ensure_ascii=False)[:600]}"
            except (json.JSONDecodeError, AttributeError):
                code, message = "", detail[:400]
            raise ProxyError(exc.code, code, message, extra) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if method == "GET" and attempt < attempts:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(
                f"连不上本机代理 {url}：{exc}。先启动 工具/symphony/启动代理.cmd 再重试。"
            ) from exc
    raise RuntimeError("GET 重试耗尽")


def account_query(account: str | None, main_account: str | None = None) -> str:
    """子账号名可能含中文（如 legacy-a），不编码会让 http.client 直接抛 UnicodeEncodeError。"""
    params: list[tuple[str, str]] = []
    if account:
        params.append(("account", str(account)))
    if main_account:
        params.append(("main_account", str(main_account)))
    return f"?{urllib.parse.urlencode(params)}" if params else ""


class ProxyError(RuntimeError):
    def __init__(self, http_status: int, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(f"HTTP {http_status} {code}: {message}")
        self.http_status = http_status
        self.code = code
        self.message = message
        self.details = details or {}


# ---------------------------------------------------------------- 账号日限标记（V29）


def quota_window_start(now: float | None = None) -> float:
    """当前额度日的起点：最近一次早上 8 点。标记只在本额度日内有效。"""
    moment = now if now is not None else time.time()
    local = time.localtime(moment)
    anchor = time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, QUOTA_RESET_HOUR, 0, 0, 0, 0, -1)
    )
    return anchor if anchor <= moment else anchor - 86400


def limit_key(main_account: str | None, account: str) -> str:
    """日限标记的键：主账号限定。两个主账号下可能有同名子账号，裸名会互相牵连。"""
    return f"{main_account}/{account}" if main_account else str(account)


def account_limit_mark(marks: dict, main_account: str | None, account: str) -> dict | None:
    """查一个子账号的日限标记。回退裸名是为了认 2026-08-01 之前写的旧标记。"""
    return marks.get(limit_key(main_account, account)) or marks.get(str(account))


def load_account_limits() -> dict:
    """本额度日内被平台判日限（10001114）的账号；过期标记读时即弃，无需手工清理。"""
    if not LIMIT_MARKS_PATH.exists():
        return {}
    try:
        raw = json.loads(LIMIT_MARKS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    start = quota_window_start()
    return {
        name: info
        for name, info in raw.items()
        if isinstance(info, dict) and float(info.get("limited_at_epoch") or 0) >= start
    }


def mark_account_limited(account: str, reason: str, main_account: str | None = None) -> None:
    """持久化日限标记：本批和之后的批都跳过该账号，明早 8 点额度刷新自动解除。"""
    marks = load_account_limits()  # 顺带把已过期的旧标记清出文件
    marks[limit_key(main_account, account)] = {
        "limited_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "limited_at_epoch": time.time(),
        "reason": str(reason)[:300],
    }
    LIMIT_MARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIMIT_MARKS_PATH.write_text(
        json.dumps(marks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------- 预检


def channel_status(base_url: str) -> dict:
    """一次探活拿齐模型表、许可证、直连登录态。不扣积分、不开浏览器。"""
    return api_json(f"{base_url}/v1/models", timeout=30)["data"]


def known_main_accounts(status: dict) -> list[str]:
    """代理里已有独立直连登录态的主账号别名，顺序即 /v1/models 返回的顺序。"""
    return [
        str(row["main_account"])
        for row in status.get("main_accounts") or []
        if row.get("main_account")
    ]


# ---------------------------------------------------------------- 今日提交计数器（V33）


TASKS_ROOT = SOURCE_DIR / "运行垃圾" / "tasks"
AUDIT_REJECT_RE = re.compile(r"video audit rejected", re.I)
# 账本各状态行的时间字段不统一，按出现频率排序逐个找
_EVENT_TIME_KEYS = (
    "submitted_at", "completed_at", "recorded_at",
    "resolved_at", "verified_at", "reconciled_at",
)


def _event_epoch(row: dict) -> float | None:
    for key in _EVENT_TIME_KEYS:
        raw = row.get(key)
        if not raw:
            continue
        # 同一批账本里 +0800 与 +08:00 两种时区写法并存；本机就是 +0800，
        # 直接取本地时间前 19 位解析即可
        try:
            return time.mktime(time.strptime(str(raw)[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
    return None


def scan_today_stats() -> dict:
    """扫全部 symphony-*.jsonl 账本，聚合本额度日（早8点起）的事件计数。

    账本是唯一数据源，不另建计数文件：不会双写漂移，并且其他并行会话
    同时段的提交也一并计入。缺时间戳或解析不了的行不计。
    """
    counts = {
        "submitted": 0, "succeeded": 0, "rejected": 0, "audit_rejected": 0,
        "account_limited": 0, "submit_failed": 0, "other_failed": 0,
    }
    per_account: dict[str, int] = {}
    start = quota_window_start()
    for path in TASKS_ROOT.rglob("symphony-*.jsonl"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            moment = _event_epoch(row)
            if moment is None or moment < start:
                continue
            status = row.get("status")
            if status == "submitted":
                counts["submitted"] += 1
                key = f"{row.get('main_account') or '?'}/{row.get('account') or '?'}"
                per_account[key] = per_account.get(key, 0) + 1
            elif status == "succeeded":
                counts["succeeded"] += 1
            elif status == "failed":
                reason = str(row.get("fail_reason") or "")
                bucket = "audit_rejected" if AUDIT_REJECT_RE.search(reason) else "other_failed"
                counts[bucket] += 1
            elif status in ("rejected", "account_limited", "submit_failed"):
                counts[status] += 1
    counts["per_account"] = per_account
    return counts


def counter_line(counts: dict | None = None) -> str:
    """一行式今日计数；提交/轮询每落一条账就回显一次，随时能看到全局水位。"""
    c = counts or scan_today_stats()
    return (
        f"COUNTER 今日8点起：提交{c['submitted']}｜成功{c['succeeded']}"
        f"｜提交被拒{c['rejected']}｜卡人脸{c['audit_rejected']}"
        f"｜限额{c['account_limited']}｜技术失败{c['submit_failed']}"
        f"｜其他失败{c['other_failed']}"
    )


def print_account_breakdown(counts: dict | None = None) -> None:
    c = counts or scan_today_stats()
    print(counter_line(c), flush=True)
    rows = sorted(c["per_account"].items(), key=lambda kv: (-kv[1], kv[0]))
    if not rows:
        print("今日尚无提交", flush=True)
        return
    print("今日各子账号提交：", flush=True)
    for key, n in rows:
        print(f"  {key:<26} {n}", flush=True)


# ---------------------------------------------------------------- 占槽溯源（V35）
# 全部批次共用同一池子账号（15 子账号 × 5 槽）。多集并行时只看总数会误判
# "明明没提交这么多怎么满了"——按账本把在渲任务归属到各批，一眼看清谁占的槽。

INFLIGHT_WINDOW_SECONDS = 30 * 60  # 提交超过 30 分钟没出结果＝账本失更（15秒片约6分钟渲完），不再算占槽
RELEASE_LAG_SECONDS = 5 * 60  # 平台对失败任务的槽位释放有约 2 分钟延迟，观察窗口放宽到 5 分钟


def slot_attribution(own_ledger: Path | None = None) -> dict:
    """按账本归属拆解「谁占着渲染槽位」。

    账本是唯一数据源，每单元取最后一条状态；只把近 30 分钟内提交、尚未出结果的
    算作在渲占槽。recent_failed＝近 5 分钟刚失败的条数：全池显示满而账本无人认领
    时，多半是这些失败任务还占着平台计数没被释放。
    """
    now = time.time()
    own_key = str(own_ledger.resolve()) if own_ledger else None
    own = 0
    foreign: dict[str, int] = {}
    recent_failed = 0
    for path in TASKS_ROOT.rglob("symphony-*.jsonl"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        last: dict = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("unit") is not None and row.get("status"):
                last[row["unit"]] = row
        for row in last.values():
            moment = _event_epoch(row)
            if moment is None:
                continue
            status = row.get("status")
            if status == "submitted" and now - moment <= INFLIGHT_WINDOW_SECONDS:
                if own_key and str(path.resolve()) == own_key:
                    own += 1
                else:
                    story = row.get("story_root")
                    label = Path(story).name if story else path.parent.name
                    foreign[label] = foreign.get(label, 0) + 1
            elif status == "failed" and now - moment <= RELEASE_LAG_SECONDS:
                recent_failed += 1
    return {"own": own, "foreign": foreign, "recent_failed": recent_failed}


def slot_attribution_line(attr: dict, platform_total: int | None = None) -> str:
    """一行式归属：本批N｜其他集N｜账本外N。账本外＝平台计数减去账本能解释的部分，
    通常就是失败任务待释放的僵尸计数。"""
    parts = [f"本批{attr['own']}"]
    parts += [
        f"{label}{n}"
        for label, n in sorted(attr["foreign"].items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    if platform_total is not None:
        ghost = platform_total - attr["own"] - sum(attr["foreign"].values())
        if ghost > 0:
            parts.append(f"账本外{ghost}")
    return "｜".join(parts)


# ---------------------------------------------------------------- 主账号


def resolve_main_accounts(
    status: dict,
    value: str | None,
    default_main: str | None,
    disabled: set[str] | frozenset[str] = frozenset(),
) -> list[str]:
    """--main-accounts 的值 → 主账号别名列表。all / 空 表示全部已登录主账号。

    传 None（没给这个参数）时退回单主账号模式，用 --main-account / 配置里的默认值。
    「停用主账号」（封号）一律排除；显式点名已停用账号直接报错，不静默换账号。
    """
    known = [name for name in known_main_accounts(status) if name not in disabled]
    if value is None:
        if default_main and str(default_main) in disabled:
            raise RuntimeError(
                f"主账号 {default_main} 已列入 symphony配置.json「停用主账号」（封号/停用），"
                "换 --main-account 指定其他账号，或改配置里的默认主账号"
            )
        return [str(default_main)] if default_main else []
    names = [name.strip() for name in value.split(",") if name.strip()]
    banned = [name for name in names if name in disabled]
    if banned:
        raise RuntimeError(
            f"--main-accounts 含已停用主账号 {banned}（symphony配置.json「停用主账号」），拒绝提交"
        )
    if not names or names == ["all"]:
        if not known:
            raise RuntimeError(
                "代理没有报告任何主账号登录态，--main-accounts all 无从展开。"
                "先用 OpenCLI direct-session --main-account 采一次登录态。"
            )
        return known
    unknown = [name for name in names if name not in known]
    if unknown:
        raise RuntimeError(
            f"--main-accounts 里有没有直连登录态的主账号: {unknown}；当前已登录：{known}"
        )
    return names


def assert_channel_ready(
    status: dict,
    model: str | None,
    main_accounts: list[str] | str | None = None,
) -> None:
    license_state = status.get("license") or {}
    if not license_state.get("valid"):
        raise RuntimeError(
            f"本机许可证不可用（{license_state.get('code')}）。GET /v1/license/status 查详情。"
        )
    expires = license_state.get("expiresAt")
    if expires:
        print(f"LICENSE ok expires_at={expires}", flush=True)
    wanted = [main_accounts] if isinstance(main_accounts, str) else list(main_accounts or [])
    if wanted:
        aliases = set(known_main_accounts(status))
        missing = [name for name in wanted if name not in aliases]
        if missing:
            raise RuntimeError(
                f"主账号 {missing} 的独立直连登录态缺失。请用对应 OpenCLI Profile "
                "重新运行 direct-session --main-account。"
            )
    else:
        session = status.get("direct_session") or {}
        if not session.get("ready"):
            raise RuntimeError(
                f"直连登录态缺失（{session.get('reason')}）。跑一次 "
                "node <OpenCLI入口> tiktok-symphony direct-session -f json 重新采集。"
            )
    if model is None:
        return
    ids = [row.get("id") for row in status.get("models") or []]
    if model not in ids:
        raise RuntimeError(f"实时模型表中没有 {model!r}，停止提交。当前可用：{ids}")


def load_asset_cache() -> dict:
    """已登记到平台的参考图，按文件内容 SHA-256 索引，跨集共享。"""
    if not ASSET_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(ASSET_CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def uncached_refs(refs: list[str], cache: dict) -> list[str]:
    """返回本批里还没入库的图。这些图第一次提交时会用隐藏后台 Chrome 上传登记（V22）。"""
    import hashlib

    missing = []
    for ref in refs:
        digest = hashlib.sha256(Path(ref).read_bytes()).hexdigest()
        if digest not in cache:
            missing.append(ref)
    return missing


def unit_duration(section: str, unit: int, round_up: bool) -> int:
    match = re.search(r"(?m)^生成参数：\s*(\d+)秒", section)
    if not match:
        raise ValueError(f"单元{unit}未找到生成时长")
    duration = int(match.group(1))
    if duration in ALLOWED_DURATIONS:
        return duration
    if not round_up:
        raise ValueError(
            f"单元{unit}时长 {duration}秒，Symphony 只有 {ALLOWED_DURATIONS} 四档。"
            "固定15秒模式不该出现这种值；自然段落模式请加 --round-duration-up 向上取整"
            "（会多扣积分且成片比脚本长），或改用支持任意秒数的通道。"
        )
    larger = [value for value in ALLOWED_DURATIONS if value >= duration]
    if not larger:
        raise ValueError(f"单元{unit}时长 {duration}秒 超过 Symphony 上限 15 秒")
    return larger[0]


def prepare_unit(story_root: Path, markdown: str, unit: int, round_up: bool) -> dict:
    section = unit_section(markdown, unit)
    prompt = video_prompt(section, unit)
    tokens = mount_tokens(section, unit)
    paths = [resolve_mount(story_root, token, unit, None) for token in tokens]
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(path)
    if len(paths) > MAX_REFS:
        raise ValueError(f"单元{unit}挂载 {len(paths)} 张，超过 Symphony 上限 {MAX_REFS} 张")
    used = [int(n) for n in re.findall(r"图片(\d+)", prompt)]
    if used and max(used) > len(paths):
        raise ValueError(f"单元{unit}图片编号超过挂载数: max={max(used)}, mounts={len(paths)}")
    return {
        "unit": unit,
        "prompt": prompt,
        "refs": [str(path) for path in paths],
        "duration": unit_duration(section, unit, round_up),
    }


def read_markdown(story_root: Path, prompt_file: str, allow_partial: bool) -> str:
    markdown = (story_root / prompt_file).read_text(encoding="utf-8")
    ready = bool(re.search(r"(?m)^(?:fenjing_ready|grids_ready):\s*true\s*$", markdown))
    if not ready and not allow_partial:
        raise RuntimeError(
            "fenjing_ready 不是 true（老集为 grids_ready）；用户未审稿放行不得提交。"
            "仅用户明确点名放行部分单元时可加 --allow-partial"
        )
    return markdown


def preflight(args: argparse.Namespace) -> int:
    base_url, local = load_config()
    main_account = args.main_account or local.get("默认主账号")
    story_root = Path(args.story_root).resolve()
    story_scope = str(story_root)
    markdown = read_markdown(story_root, args.prompt_file, args.allow_partial)
    status = channel_status(base_url)
    mains = resolve_main_accounts(status, args.main_accounts, main_account, disabled_main_accounts(local))
    assert_channel_ready(status, args.model, mains)
    if args.main_accounts is not None:
        print(f"MAIN_ACCOUNTS {mains}", flush=True)
    for row in status.get("models") or []:
        flag = " ★推荐" if row.get("recommended") else ""
        print(f"MODEL {row.get('id')}{flag} durations={row.get('durations')} max_refs={row.get('max_refs')}", flush=True)

    account = args.account or local.get("默认账号")
    cache = load_asset_cache()
    total = 0
    pending_upload: set[str] = set()
    for unit in parse_units(args.units):
        prepared = prepare_unit(story_root, markdown, unit, args.round_duration_up)
        total += prepared["duration"]
        names = [Path(ref).name for ref in prepared["refs"]]
        missing = uncached_refs(prepared["refs"], cache)
        pending_upload.update(missing)
        print(
            f"PREFLIGHT unit={unit} duration={prepared['duration']}s refs={len(names)} "
            f"新图={len(missing)} {names}",
            flush=True,
        )

    # V22：判断这批能不能全静默跑完
    if pending_upload:
        print(
            f"SILENT no —— {len(pending_upload)} 张图未入库，backend=auto 会用隐藏后台窗口"
            "各上传登记一次（每图一辈子一次），提交会变慢；backend=direct 则会在第一张新图上直接失败：",
            flush=True,
        )
        for ref in sorted(pending_upload):
            size_kb = Path(ref).stat().st_size / 1024
            hint = "  ← 建议压到 1024px JPEG，上传往返降到约 1/8" if size_kb > 600 else ""
            print(f"  UNCACHED {Path(ref).name} ({size_kb:.0f} KB){hint}", flush=True)
    else:
        print("SILENT yes —— 参考图全部已入库，本批可用 backend=direct 全程零浏览器", flush=True)

    unit_count = len(parse_units(args.units))
    marks = load_account_limits()
    if args.accounts is not None or args.main_accounts is not None:
        # 轮转模式：积分与 5 条并发额度都是每账号独立的，所以两者都按池求和。
        # 跨主账号时池 = 各主账号子账号的并集，求和口径不变。
        # V29：已标记日限的账号不计入池（提交时调度器同样会跳过它们）。
        rows = multi_account_pool(base_url, parse_accounts(args.accounts), mains)
        usable_rows = []
        available = 0
        slots = 0
        for row in rows:
            label = account_label(row)
            if "error" in row:
                print(f"ACCOUNT {label} 不可用: {row['error']}", flush=True)
                continue
            mark = account_limit_mark(marks, row.get("main_account"), str(row["name"]))
            if mark:
                print(
                    f"ACCOUNT {label} 已标记当日日限（{mark.get('limited_at')} 触发，"
                    f"明早{QUOTA_RESET_HOUR}点刷新解除），本批不计入",
                    flush=True,
                )
                continue
            usable_rows.append(row)
            limit = min(args.max_inflight, int(row.get("limit") or MAX_INFLIGHT))
            available += int(row.get("credits") or 0)
            slots += max(0, limit - int(row.get("generating") or 0))
            print(
                f"ACCOUNT {label} credits={row['credits']} generating={row['generating']}/{limit}",
                flush=True,
            )
        if not usable_rows:
            print(
                f"ERROR 池内账号全部已标记当日日限，明早{QUOTA_RESET_HOUR}点额度刷新后再投",
                file=sys.stderr, flush=True,
            )
            return 1
        print(f"BUDGET total_credits={total} pool_credits={available} remaining_after={available - total}", flush=True)
        if total > available:
            print("ERROR 全池积分不足，提交会中途卡住", file=sys.stderr, flush=True)
            return 1
        capacity = sum(min(args.max_inflight, int(r.get("limit") or MAX_INFLIGHT)) for r in usable_rows)
        print(
            f"THROTTLE 池并发硬上限={capacity} 条（当前空位 {slots}），本批 units={unit_count}；"
            "全量任务按空位分批提交，满额时等待；端到端时长受平台负载支配，不作公式估算",
            flush=True,
        )
        flags = f"--confirm-credits {total} --accounts {args.accounts or 'all'}"
        if args.main_accounts is not None:
            flags += f" --main-accounts {args.main_accounts}"
        print(f"OK 预检通过。提交时加 {flags}", flush=True)
        return 0

    mark = account_limit_mark(marks, main_account, account) if account else None
    if mark:
        print(
            f"ERROR 账号 {account} 已标记当日日限（{mark.get('limited_at')} 触发，"
            f"明早{QUOTA_RESET_HOUR}点刷新解除），换账号或届时再投",
            file=sys.stderr, flush=True,
        )
        return 1
    credits = api_json(
        f"{base_url}/v1/direct/credits" + account_query(account, main_account),
        timeout=60,
    )["data"]
    print(
        f"ACCOUNT {credits.get('name')} credits={credits.get('credits')} weekly_spent={credits.get('weeklySpent')}",
        flush=True,
    )
    print(f"BUDGET total_credits={total} remaining_after={int(credits.get('credits') or 0) - total}", flush=True)

    # 单子账号同时只能渲 5 条；本地始终按配置上限节流，不把“平台能排队”当成放宽授权。
    try:
        current, limit = generating_count(base_url, account, main_account)
        limit = min(args.max_inflight, limit)
        print(
            f"THROTTLE inflight={current}/{limit} units={unit_count}；达到上限后等待空位，"
            "全量任务分批完成；端到端时长受平台负载支配，不作公式估算",
            flush=True,
        )
    except (ProxyError, RuntimeError) as exc:
        print(f"THROUGHPUT 查在渲条数失败: {exc}", flush=True)

    if total > int(credits.get("credits") or 0):
        print("ERROR 积分不足，提交会中途失败", file=sys.stderr, flush=True)
        return 1
    print(f"OK 预检通过。提交时加 --confirm-credits {total}", flush=True)
    return 0


# ---------------------------------------------------------------- 热预存


def main_account_sub_names(status: dict, main_account: str) -> list[str]:
    """/v1/models 的 main_accounts 里已经带了各主账号的子账号名单，不用再发请求。"""
    for row in status.get("main_accounts") or []:
        if str(row.get("main_account")) == main_account:
            return [str(item.get("name")) for item in row.get("accounts") or [] if item.get("name")]
    return []


def pick_warmup_target(
    status: dict, args: argparse.Namespace, local: dict,
) -> tuple[str, str | None] | None:
    """挑一个能开浏览器干活的「主账号/子账号」对。

    warmup 走的是网页端（OpenCLI），而**当天额度用尽的子账号进不去创作面板**——
    表现是 OpenCLI 报「无法切换到 Dreamina Seedance 2.0」，而不是一个账号相关的错误，
    很容易被误读成模型或平台的问题（2026-08-01 38集踩过）。

    关键在粒度：日限按**子账号**算（V29），而网页端会话停在浏览器 profile 上次
    活跃的 workspace 上——主账号"还有余量"救不了停在已限额子账号里的会话
    （2026-08-01 38集二次踩坑：main-b 还剩 7 个子账号可用，两次热预存仍连续
    失败，就因为会话落在刚被限额的子账号上）。所以这里直接选到子账号，由代理
    在进创作面板前先 `account --use` 切过去。

    候选顺序：--main-account（显式指定，最高优先）→ --main-accounts 展开的列表 →
    配置里的默认主账号。全部被限则返回 None，由调用方停批。
    """
    default_main = local.get("默认主账号")
    disabled = disabled_main_accounts(local)
    if args.main_account:
        candidates = [args.main_account]
    elif args.main_accounts is not None:
        candidates = resolve_main_accounts(status, args.main_accounts, default_main, disabled)
    else:
        candidates = [str(default_main)] if default_main else []
    live = [name for name in candidates if str(name) not in disabled]
    if len(live) != len(candidates):
        print(
            f"WARMUP_SKIP 已停用主账号 {sorted(set(map(str, candidates)) - set(live))} 不参与热预存",
            flush=True,
        )
    candidates = live
    if not candidates:
        return None

    marks = load_account_limits()
    if args.account:
        # warmup 也必须尊重显式子账号选择；此前只按列表取第一个空闲账号，
        # 遇到网页账号菜单打不开时无法切换到下一个可用 workspace。
        for main in candidates:
            names = main_account_sub_names(status, main)
            if args.account in names:
                if account_limit_mark(marks, main, args.account):
                    print(
                        f"WARMUP_SKIP {main}/{args.account} 已标记当日日限，不能用于热预存",
                        flush=True,
                    )
                    return None
                return main, args.account
        return None
    for main in candidates:
        names = main_account_sub_names(status, main)
        if not names:
            # 拿不到子账号名单时不臆断，按可用处理、不指定切号，让它去跑真实结果
            return main, None
        free = [name for name in names if not account_limit_mark(marks, main, name)]
        if free:
            return main, free[0]
        print(
            f"WARMUP_SKIP {main} 的 {len(names)} 个子账号今天全部已达日限，"
            "网页端进不了创作面板，跳过换下一个主账号",
            flush=True,
        )
    return None


def warmup(args: argparse.Namespace) -> int:
    """一次把本批所有没入库的参考图上传登记，之后整批就能纯直连跑（V22）。

    底层走 OpenCLI 的 prepare-only：上传、写缓存、不点提交按钮，**不扣积分**。
    每次最多 9 张（平台参考图上限），超出自动分批。
    """
    base_url, local = load_config()
    story_root = Path(args.story_root).resolve()
    markdown = read_markdown(story_root, args.prompt_file, args.allow_partial)
    status = channel_status(base_url)
    # warmup 只需要一个主账号（asset_cache.json 是全局 SHA-256→URL 表，跨主账号可复用，
    # 2026-08-01 实测），但**必须落在当天还没被限额的子账号上**：额度用尽的子账号在
    # 网页端进不去创作面板，OpenCLI 会卡在「无法切换到 Dreamina Seedance 2.0」，
    # 而会话默认停在 profile 上次活跃的 workspace——所以选到子账号并让代理先切号。
    target = pick_warmup_target(status, args, local)
    if target is None:
        print(
            "ERROR 候选主账号的子账号今天全部已达日限，网页端进不了创作面板，热预存必然失败；"
            f"明早{QUOTA_RESET_HOUR}点额度刷新后再跑，或用 --main-account 指定一个还有额度的主账号",
            file=sys.stderr, flush=True,
        )
        return 1
    main_account, sub_account = target
    if args.warmup_current:
        # 调试/恢复入口：当 OpenCLI 账号菜单不可用时，复用该主账号浏览器
        # profile 当前已登录的 workspace，不再触发 native-click 切号。
        sub_account = None
    assert_channel_ready(status, None, main_account)
    print(
        f"WARMUP_MAIN {main_account}/{sub_account or '（当前workspace，子账号名单不可得）'}"
        " —— 参考图入库只做一次，之后全部主账号共用",
        flush=True,
    )

    cache = load_asset_cache()
    todo: list[str] = []
    for unit in parse_units(args.units):
        for ref in prepare_unit(story_root, markdown, unit, args.round_duration_up)["refs"]:
            if ref not in todo:
                todo.append(ref)
    todo = uncached_refs(todo, cache)
    if not todo:
        print("SILENT yes —— 本批参考图全部已入库，无需热预存", flush=True)
        return 0

    print(f"WARMUP {len(todo)} 张待入库（不扣积分，每批最多 {MAX_REFS} 张）", flush=True)
    for start in range(0, len(todo), MAX_REFS):
        chunk = todo[start:start + MAX_REFS]
        print(f"UPLOADING {[Path(r).name for r in chunk]}", flush=True)
        try:
            data = api_json(
                f"{base_url}/v1/assets/warmup",
                method="POST",
                payload={
                    "refs": chunk,
                    "timeout": args.warmup_timeout,
                    **({"main_account": main_account} if main_account else {}),
                    **({"account": sub_account} if sub_account else {}),
                },
                timeout=args.warmup_timeout + 60,
            )["data"]
            print(f"CACHED newly={data.get('newly_cached')} still_missing={data.get('still_missing')}", flush=True)
        except (ProxyError, RuntimeError) as exc:
            # OpenCLI 在写完缓存之后仍可能非零退出（prepare-only 分支实测如此）。
            # 成败一律以缓存实际内容为准，不看退出码——最后统一复核。
            print(f"WARMUP_CLI_NONZERO {exc}", file=sys.stderr, flush=True)
            if "could not switch" in str(exc).lower() or "无法切换" in str(exc):
                print(
                    "HINT 「无法切换」= 账号额度问题不是模型问题（V31）：当前子账号大概率已达日限"
                    "但还没被写盘标记；确认后可手工补记进 工具/symphony/data/账号限制.json 再重跑",
                    file=sys.stderr, flush=True,
                )

    remaining = uncached_refs(todo, load_asset_cache())
    if remaining:
        print(f"SILENT no —— 仍有 {len(remaining)} 张未入库：{[Path(r).name for r in remaining]}", file=sys.stderr, flush=True)
        return 1
    print("SILENT yes —— 全部入库完成，本批可用 --backend direct 全程零浏览器", flush=True)
    return 0


# ---------------------------------------------------------------- 提交


def parse_accounts(value: str | None) -> list[str] | None:
    """--accounts 的值 → 账号白名单。all / 空 表示不限制，用全部子账号。"""
    if value is None:
        return None
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names or names == ["all"]:
        return None
    return names


def account_pool(
    base_url: str,
    allowed: list[str] | None,
    main_account: str | None = None,
    strict: bool = True,
) -> list[dict]:
    """全部子账号的 {name, credits, generating, limit}；查不动的账号带 error 字段。

    strict=False 用于跨主账号合并：单个主账号里找不到某个白名单名字是正常的
    （它属于另一个主账号），未知名的检查要挪到并集那一层做。
    """
    url = f"{base_url}/v1/direct/pool"
    params = [("accounts", name) for name in allowed or []]
    if main_account:
        params.append(("main_account", str(main_account)))
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"
    rows = api_json(url, timeout=180)["data"]
    for row in rows:
        if "error" not in row:
            # 代理提交闸门里的短时保留（已提交、平台 generating 计数未跟上）同样占槽位，
            # 折进本地视图统一按 generating 记账；旧版代理没有 reserved 字段时 +0 不变。
            row["generating"] = int(row.get("generating") or 0) + int(row.get("reserved") or 0)
    if allowed:
        rows = [row for row in rows if row.get("name") in allowed]
        if strict:
            unknown = set(allowed) - {row.get("name") for row in rows}
            if unknown:
                raise RuntimeError(f"--accounts 里有不存在的子账号: {sorted(unknown)}")
    return rows


def pool_key(row: dict) -> tuple[str | None, str]:
    """账号池的主键。**必须带主账号**：两个主账号下可以有同名子账号（如都叫 "1"），
    只用子账号名做键会让两条不同的账号互相覆盖，路由到错误的登录态。"""
    main = row.get("main_account")
    return (str(main) if main else None, str(row["name"]))


def account_label(row: dict) -> str:
    main = row.get("main_account")
    return f"{main}/{row['name']}" if main else str(row["name"])


def multi_account_pool(
    base_url: str,
    allowed: list[str] | None,
    mains: list[str] | None,
) -> list[dict]:
    """跨主账号合并账号池，每行补上 main_account 字段。

    代理的 /v1/direct/pool 一次只认一个 main_account（一个主账号一份登录态），
    所以这里按主账号逐个查再拼起来。
    """
    if not mains:
        return [dict(row, main_account=None) for row in account_pool(base_url, allowed, None)]
    rows: list[dict] = []
    for main in mains:
        for row in account_pool(base_url, allowed, main, strict=False):
            rows.append({**row, "main_account": main})
    if allowed:
        unknown = set(allowed) - {str(row["name"]) for row in rows}
        if unknown:
            raise RuntimeError(
                f"--accounts 里有不存在的子账号: {sorted(unknown)}（已在主账号 {mains} 下找过）"
            )
    return rows


class PoolScheduler:
    """账号池调度器：本地记账 + 按需刷新。

    每提交一条就重查一遍全池要 ~14 秒（服务端 25 趟 TikTok 请求），而单条提交本身只要
    ~5 秒——于是稳态并发被卡在 360/19.3 ≈ 19 条，永远摸不到 25 的真实上限。
    改成本地维护在渲计数：提交成功就给该账号 +1，只有本地判定全池已满时才真去查。
    本地只增不减（假设没有任何任务完成），所以只会少提交、不会超限。
    """

    def __init__(
        self,
        base_url: str,
        allowed: list[str] | None,
        max_inflight: int,
        mains: list[str] | str | None = None,
    ) -> None:
        self.base_url = base_url
        self.allowed = allowed
        self.max_inflight = max_inflight
        # 单主账号也走同一条路径，只是列表里只有一个元素
        self.mains = [mains] if isinstance(mains, str) else list(mains or [])
        self.rows: dict[tuple[str | None, str], dict] = {}
        self.known_accounts: set[tuple[str | None, str]] = set()
        self.disabled_accounts: set[tuple[str | None, str]] = set()
        self.marks = load_account_limits()
        if self.marks:
            print(
                f"ACCOUNT_LIMIT_SKIP 今日已标记日限账号 {sorted(self.marks)}，跳过不投"
                f"（明早{QUOTA_RESET_HOUR}点额度刷新自动解除）",
                flush=True,
            )
        self.refresh()

    def refresh(self) -> None:
        fresh = [
            row
            for row in multi_account_pool(self.base_url, self.allowed, self.mains)
            if "error" not in row
        ]
        for row in fresh:
            key = pool_key(row)
            self.known_accounts.add(key)
            if account_limit_mark(self.marks, row.get("main_account"), str(row["name"])):
                self.disabled_accounts.add(key)
        self.rows = {
            pool_key(row): row for row in fresh
            if pool_key(row) not in self.disabled_accounts
        }

    def _limit(self, row: dict) -> int:
        return min(self.max_inflight, int(row.get("limit") or MAX_INFLIGHT))

    def _candidates(self, need_credits: int) -> list[dict]:
        usable = [
            row for row in self.rows.values()
            if int(row.get("generating") or 0) < self._limit(row)
            and int(row.get("credits") or 0) >= need_credits
        ]
        usable.sort(key=lambda row: (int(row.get("generating") or 0), -int(row.get("credits") or 0)))
        return usable

    def pick(self, need_credits: int) -> dict | None:
        """先用本地视图挑；本地说满了才刷新一次再挑。"""
        candidates = self._candidates(need_credits)
        if candidates:
            return candidates[0]
        self.refresh()
        candidates = self._candidates(need_credits)
        return candidates[0] if candidates else None

    def charge(self, key: tuple[str | None, str], credits: int) -> None:
        """提交成功后本地记账，避免下一条又去查全池。"""
        row = self.rows.get(key)
        if row:
            row["generating"] = int(row.get("generating") or 0) + 1
            row["credits"] = int(row.get("credits") or 0) - credits

    def disable(self, key: tuple[str | None, str]) -> None:
        """移除已达到账号级日限的子账号，防止后续单元继续路由到它。"""
        self.disabled_accounts.add(key)
        self.rows.pop(key, None)

    def all_accounts_disabled(self) -> bool:
        return bool(self.known_accounts) and self.known_accounts <= self.disabled_accounts

    def summary(self) -> str:
        return " ".join(
            f"{account_label(row)}={row.get('generating')}/{self._limit(row)}"
            for row in self.rows.values()
        )

    def inflight(self) -> int:
        return sum(int(row.get("generating") or 0) for row in self.rows.values())


def generating_count(
    base_url: str,
    account: str | None,
    main_account: str | None = None,
) -> tuple[int, int]:
    url = f"{base_url}/v1/direct/generating-count" + account_query(account, main_account)
    data = api_json(url, timeout=60)["data"]
    # reserved＝代理提交闸门里的短时保留额度，同样占槽位（旧版代理没有该字段时 +0）
    occupied = int(data.get("generating") or 0) + int(data.get("reserved") or 0)
    return occupied, int(data.get("limit") or MAX_INFLIGHT)


def wait_for_slot(
    base_url: str,
    account: str | None,
    max_inflight: int,
    interval: int,
    main_account: str | None = None,
) -> bool:
    """等到该子账号有空闲渲染槽位。单账号上限 5 条，满了还硬推平台会拒收。

    15 秒片渲染约 6 分钟，所以这里等几分钟是正常的，不是卡死。
    """
    deadline = time.time() + 7200
    while time.time() < deadline:
        try:
            current, platform_limit = generating_count(base_url, account, main_account)
        except (ProxyError, RuntimeError) as exc:
            print(f"THROTTLE 查在渲条数失败，保守等待: {exc}", flush=True)
            time.sleep(max(interval, 30))
            continue
        limit = min(max_inflight, platform_limit)
        if current < limit:
            return True
        print(f"THROTTLE inflight={current}/{limit} 等空位…", flush=True)
        time.sleep(max(interval, 30))
    return False


def submit(args: argparse.Namespace) -> int:
    base_url, local = load_config()
    main_account = args.main_account or local.get("默认主账号")
    story_root = Path(args.story_root).resolve()
    story_scope = str(story_root)
    markdown = read_markdown(story_root, args.prompt_file, args.allow_partial)
    status = channel_status(base_url)
    mains = resolve_main_accounts(status, args.main_accounts, main_account, disabled_main_accounts(local))
    assert_channel_ready(status, args.model, mains)
    if args.main_accounts is not None:
        print(f"MAIN_ACCOUNTS {mains}", flush=True)

    units = parse_units(args.units)
    prepared = [prepare_unit(story_root, markdown, unit, args.round_duration_up) for unit in units]

    # 先按账本剔掉已提交的，再算预算——--confirm-credits 的语义是「这一次实际会扣多少」，
    # 不是「命令里点名了多少」。续跑时已提交的单元不会重扣，数字必须跟着变。
    ledger = Path(args.ledger).resolve()
    if ledger.exists() and ledger.stat().st_size:
        if not args.resume:
            raise FileExistsError(
                f"账本已存在且非空，拒绝重复提交: {ledger}。续跑同一批加 --resume。"
            )
        # 按单元取**最后一条**状态：同一单元可能先失败后被人工核实改判，
        # 只看有没有出现过某状态会把已澄清的单元永久锁死。
        last_status: dict[int | float, str] = {}
        for entry in load_ledger(ledger):
            if entry.get("unit") is not None and entry.get("status"):
                last_status[entry["unit"]] = str(entry["status"])
        # submit_failed / 已核实没提交 的单元要重投；unknown_outcome 未核实前一律不碰
        blocked = {"submitted", "succeeded", "unknown_outcome"}
        already = {unit for unit, state in last_status.items() if state in blocked}
        skipped = [item["unit"] for item in prepared if item["unit"] in already]
        prepared = [item for item in prepared if item["unit"] not in already]
        print(
            f"RESUME 账本已有 {len(skipped)} 个单元（{skipped}），本次只提交剩下的 {len(prepared)} 个",
            flush=True,
        )
        if not prepared:
            print("NOTHING_TO_SUBMIT 全部单元都已在账本里", flush=True)
            return 0

    total = sum(item["duration"] for item in prepared)
    if args.confirm_credits != total:
        raise RuntimeError(
            f"预算护栏：本次实际会扣 {total} 积分（{len(prepared)} 个单元），"
            f"--confirm-credits 给的是 {args.confirm_credits}。"
            "这个数字必须等于本次真实扣款——续跑时已提交的单元不重扣，所以比 preflight 的总数小。"
        )
    ledger.parent.mkdir(parents=True, exist_ok=True)
    # 开工先看别的批占了多少槽：多集并行是常态，不提前说清会误判"槽位满了没实时更新"
    attr = slot_attribution(ledger)
    if attr["foreign"]:
        others = "、".join(
            f"{label} {n} 条"
            for label, n in sorted(attr["foreign"].items(), key=lambda kv: (-kv[1], kv[0]))
        )
        print(
            f"WARNING 其他批还在渲占槽：{others}——全部批次共用同一池子账号，"
            "本批空位相应变少，属正常抢位不是卡死",
            flush=True,
        )
    lock = threading.Lock()
    fixed_account = args.account or local.get("默认账号")
    pool = parse_accounts(args.accounts)
    # --main-accounts 单独给也算轮转：跨主账号本身就要求在池里挑账号，
    # 此时 --accounts 省略等同于 all（该主账号下全部子账号）。
    rotate = args.accounts is not None or args.main_accounts is not None
    if not rotate:
        marks = load_account_limits()
        mark = account_limit_mark(marks, main_account, fixed_account) if fixed_account else None
        if mark:
            raise RuntimeError(
                f"账号 {fixed_account} 已标记当日日限"
                f"（{mark.get('limited_at')} 触发，"
                f"明早{QUOTA_RESET_HOUR}点额度刷新自动解除）。"
                "换 --account/--accounts 改投其他账号，或额度刷新后再提交。"
            )
    scheduler = (
        PoolScheduler(base_url, pool, args.max_inflight, mains)
        if rotate
        else None
    )
    if scheduler:
        print(
            f"POOL 起始 [{scheduler.summary()}] 共 {scheduler.inflight()} 条在渲"
            f"（{slot_attribution_line(attr, scheduler.inflight())}）",
            flush=True,
        )

    # 提交是串行的、渲染是服务端并行的。无论平台是否接受超额排队，本地都必须
    # 遵守用户配置的单账号并发上限：轮转模式挑空闲账号，单账号模式满额就等待。
    errors = 0
    for item in prepared:
        unit = item["unit"]
        if rotate:
            chosen = scheduler.pick(item["duration"])
            waited = time.time()
            while chosen is None:
                if scheduler.all_accounts_disabled():
                    print(
                        f"ABORTED unit={unit} 全部子账号均达到当日生成上限（已写日限标记，"
                        f"明早{QUOTA_RESET_HOUR}点额度刷新自动解除），"
                        "剩余单元未提交（届时用 --resume 续跑）",
                        file=sys.stderr,
                        flush=True,
                    )
                    return 1
                if time.time() - waited > 7200:
                    print(f"ABORTED unit={unit} 全池等待超时，剩余单元未提交（--resume 可续跑）", file=sys.stderr, flush=True)
                    return 1
                attr = slot_attribution(ledger)
                hint = (
                    f"，其中约{attr['recent_failed']}条为刚失败任务待平台释放（延迟约2分钟）"
                    if attr["recent_failed"]
                    else ""
                )
                print(
                    f"THROTTLE 全池满 [{scheduler.summary()}] 共 {scheduler.inflight()} 条在渲"
                    f"（{slot_attribution_line(attr, scheduler.inflight())}）{hint}，等空位…",
                    flush=True,
                )
                time.sleep(max(args.interval, 30))
                try:
                    scheduler.refresh()
                    chosen = scheduler.pick(item["duration"])
                except (ProxyError, RuntimeError) as exc:
                    # 一次代理抖动/重启不该炸掉一个跑 40 分钟的批次——下一轮再试
                    print(f"THROTTLE 刷新失败，下轮重试: {exc}", flush=True)
                    chosen = None
            chosen_key = pool_key(chosen)
            account = chosen["name"]
            # 跨主账号轮转时，提交必须带上**这个子账号所属的**主账号，
            # 不能用批次级的默认主账号——否则代理会拿错登录态去投。
            unit_main = chosen.get("main_account") or main_account
            print(
                f"ROUTE unit={unit} → {account_label(chosen)} "
                f"(本地视图 {chosen['generating']}/{scheduler._limit(chosen)}, "
                f"全池在渲 {scheduler.inflight()})",
                flush=True,
            )
        else:
            chosen_key = None
            account = fixed_account
            unit_main = main_account
            if not wait_for_slot(
                base_url,
                account,
                args.max_inflight,
                args.interval,
                main_account,
            ):
                print(f"ABORTED unit={unit} 等待渲染槽位超时，剩余单元未提交（--resume 可续跑）", file=sys.stderr, flush=True)
                return 1
        payload = {
            "prompt": item["prompt"],
            "refs": item["refs"],
            "mode": "reference",
            "model": args.model,
            "duration": item["duration"],
            "wait": False,
            "backend": args.backend,
        }
        if account:
            payload["account"] = account
        if unit_main:
            payload["main_account"] = unit_main
        try:
            response = api_json(f"{base_url}/v1/videos/generations", method="POST", payload=payload, timeout=args.submit_timeout)
        except ProxyError as exc:
            if rotate and exc.code == "account_capacity_exhausted":
                # 池视图已计入代理的 reserved，但「查池→提交」之间别的进程仍可能
                # 抢先占掉槽位（多批并行时）。代理提交闸门是最终仲裁：429 不建任务
                # 不扣积分。这里把该账号在本地视图标满、单元放回队尾即可。
                row = scheduler.rows.get(chosen_key)
                if row:
                    row["generating"] = scheduler._limit(row)
                prepared.append(item)
                print(
                    f"CAPACITY unit={unit} account={unit_main}/{account} 暂满，未建任务未扣积分；"
                    "改投其他账号或等待空位",
                    flush=True,
                )
                continue
            if exc.code == "direct_rejected":
                platform_code = str(exc.details.get("platform_code") or "")
                if platform_code == "10001114":
                    # 账号达到当日生成上限：没建任务、没扣积分，也不是内容拒绝。
                    # V29：写盘持久化标记，本批与之后的批都不再投它，明早8点额度刷新自动解除。
                    if account:
                        mark_account_limited(account, exc.message, unit_main)
                    append_ledger(ledger, {
                        "platform": PLATFORM, "unit": unit, "story_root": story_scope, "model": args.model,
                        "duration": item["duration"], "task_id": None, "account": account,
                        "main_account": unit_main,
                        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "status": "account_limited", "fail_reason": exc.message,
                    }, lock)
                    if rotate:
                        # 当前单元放回队尾，改投其余账号（可能落到另一个主账号下）。
                        scheduler.marks = load_account_limits()
                        scheduler.disable(chosen_key)
                        prepared.append(item)
                        print(
                            f"ACCOUNT_LIMIT unit={unit} account={unit_main}/{account} 未建任务未扣积分；"
                            f"已写日限标记（明早{QUOTA_RESET_HOUR}点自动解除），单元改投其他账号",
                            flush=True,
                        )
                        print(counter_line(), flush=True)
                        continue
                    print(
                        f"ABORTED unit={unit} account={account or '默认账号'} 达到当日生成上限，"
                        f"已写日限标记（明早{QUOTA_RESET_HOUR}点自动解除）；剩余单元未提交——"
                        "换 --accounts 改投其他账号，或额度刷新后 --resume 续跑",
                        file=sys.stderr, flush=True,
                    )
                    return 1
                # 平台明确拒绝（内容审核等）：没建任务没扣分，跳过这条继续跑整批。
                # 不臆测具体触发词（V15），原样记录平台返回的 code 与 message。
                errors += 1
                append_ledger(ledger, {
                    "platform": PLATFORM, "unit": unit, "story_root": story_scope, "model": args.model,
                    "duration": item["duration"], "task_id": None, "account": account,
                    "main_account": unit_main,
                    "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "status": "rejected", "fail_reason": exc.message,
                }, lock)
                print(f"REJECTED unit={unit} {exc.message}", file=sys.stderr, flush=True)
                print(counter_line(), flush=True)
                continue
            if exc.code == "direct_outcome_unknown":
                append_ledger(ledger, {
                    "platform": PLATFORM, "unit": unit, "story_root": story_scope, "model": args.model,
                    "duration": item["duration"], "task_id": None,
                    "main_account": unit_main, "account": account,
                    "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "status": "unknown_outcome", "fail_reason": exc.message,
                }, lock)
                print(
                    f"UNKNOWN_OUTCOME unit={unit} —— 这一条可能已经提交并扣分，代理已主动抑制重试。"
                    "先去平台 generations 列表人工确认，再决定是否重投。整批已停止。",
                    file=sys.stderr, flush=True,
                )
                return 2
            errors += 1
            append_ledger(ledger, {
                "platform": PLATFORM, "unit": unit, "story_root": story_scope, "model": args.model,
                "duration": item["duration"], "task_id": None,
                "main_account": unit_main, "account": account,
                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "status": "submit_failed", "fail_reason": str(exc),
            }, lock)
            print(f"ERROR unit={unit} {exc}", file=sys.stderr, flush=True)
            print(counter_line(), flush=True)
            continue
        rows = response.get("data") or []
        task_id = rows[0].get("taskId") if rows and isinstance(rows[0], dict) else None
        if not task_id:
            errors += 1
            print(f"ERROR unit={unit} 响应缺少 taskId: {json.dumps(response, ensure_ascii=False)[:300]}", file=sys.stderr, flush=True)
            continue
        append_ledger(ledger, {
            "platform": PLATFORM, "unit": unit, "story_root": story_scope, "model": args.model,
            "duration": item["duration"], "task_id": str(task_id),
            # 账本的 main_account/account 是 poll 逐单元路由的唯一依据，跨主账号时
            # 一定要落到**实际提交的那一对**，代理没回就用本地路由结果兜底。
            "main_account": rows[0].get("mainAccount") or unit_main,
            "account": rows[0].get("account") or account, "backend": rows[0].get("backend"),
            "refs": [Path(ref).name for ref in item["refs"]],
            "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "status": "submitted",
        }, lock)
        if scheduler:
            scheduler.charge(chosen_key, item["duration"])
        print(
            f"SUBMITTED unit={unit} task_id={task_id} duration={item['duration']}s "
            f"account={unit_main}/{account} backend={rows[0].get('backend')}",
            flush=True,
        )
        print(counter_line(), flush=True)
    print_account_breakdown()
    return 1 if errors else 0


# ---------------------------------------------------------------- 轮询与下载


def ffprobe_resolution(ffprobe: str, path: Path) -> str | None:
    """低清播放转码流(360x640)是合法 mp4、时长也对，肉眼看不出来，必须探一次。"""
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def poll(args: argparse.Namespace) -> int:
    base_url, local = load_config()
    story_root = Path(args.story_root).resolve()
    ledger = Path(args.ledger).resolve()
    entries = load_ledger(ledger)
    story_scope = str(story_root)
    if any(entry.get("story_root") for entry in entries):
        entries = [entry for entry in entries if entry.get("story_root") == story_scope]
    selected_units = set(parse_units(args.units)) if args.units else None
    if selected_units is not None:
        entries = [entry for entry in entries if entry.get("unit") in selected_units]
        print(f"POLL_UNITS {sorted(selected_units)}", flush=True)
    # 成片跟随分镜文件所在目录（模式子文件夹）；根目录分镜落 story_root/成片（老集兼容）
    output_dir = (story_root / args.prompt_file).parent / "成片"
    output_dir.mkdir(parents=True, exist_ok=True)
    ffprobe = str(local.get("ffprobe") or "ffprobe")
    account = args.account or local.get("默认账号")
    main_account = args.main_account or local.get("默认主账号")
    if args.main_accounts is not None:
        # 轮询不需要账号池：每个单元查哪个账号由账本里记的 main_account/account 决定。
        print("NOTE poll 忽略 --main-accounts，逐单元按账本记录的账号路由", flush=True)

    # 按单元取**最后一条**状态，不能用"出现过某终态"来判定——同一单元可能
    # 作废后又重投（submitted → rejected_by_policy → submitted），只看历史会把
    # 重投的那条永久排除在轮询外。这个坑在 --resume 里踩过一次，此处同源。
    # rejected_by_policy＝人工判定不合格（如撤了参考图破坏同脸），即使渲染成功也不收。
    last_entry: dict[int | float, dict] = {}
    for entry in entries:
        if entry.get("unit") is not None and entry.get("status"):
            last_entry[entry["unit"]] = entry
    done = {
        unit for unit, entry in last_entry.items()
        if entry.get("status") in {"succeeded", "failed", "rejected_by_policy"}
    }
    # 跨账号轮转后每个单元落在不同子账号，查任务和下载都必须用**该单元提交时的账号**，
    # 不能用一个全局账号——否则会在别人的账号里查不到任务。
    pending = {
        unit: (
            str(entry["task_id"]),
            entry.get("main_account") or main_account,
            entry.get("account") or account,
        )
        for unit, entry in last_entry.items()
        if entry.get("status") == "submitted" and entry.get("task_id") and unit not in done
    }
    # 已停用（封号）主账号名下的任务不再轮询：账号已死任务不会有结果，查询本身还在
    # 撞封禁账号的风控。账本不动，单元保持 submitted，解封或重投时再处理。
    dead_mains = disabled_main_accounts(local)
    if dead_mains:
        skipped = {
            unit for unit, (_tid, unit_main, _acc) in pending.items()
            if (unit_main or "") in dead_mains
        }
        for unit in sorted(skipped):
            print(
                f"SKIP_DISABLED unit={unit} main_account={pending[unit][1]} 已停用，不轮询",
                flush=True,
            )
            pending.pop(unit)
    if not pending:
        print("NOTHING_PENDING 账本里没有待轮询的单元", flush=True)
        return 0

    lock = threading.Lock()
    deadline = time.time() + args.timeout
    final_errors: dict[int | float, str] = {}

    while pending and time.time() < deadline:
        for idx, (unit, (task_id, unit_main_account, unit_account)) in enumerate(list(pending.items())):
            # 同轮各单元查询错开几秒，避免瞬时突发一串请求（风控敏感，与 --interval 同因）
            if idx:
                time.sleep(3)
            url = f"{base_url}/v1/direct/tasks/{task_id}" + account_query(
                unit_account,
                unit_main_account,
            )
            try:
                data = api_json(url, timeout=60)["data"]
            except (ProxyError, RuntimeError) as exc:
                print(f"STATUS unit={unit} query_error {exc}", flush=True)
                continue
            asset_id = data.get("assetId")
            error = data.get("error")
            if error:
                final_errors[unit] = str(error)
                append_ledger(ledger, {
                    "platform": PLATFORM, "unit": unit, "task_id": task_id,
                    "story_root": story_scope,
                    "main_account": unit_main_account, "account": unit_account,
                    "status": "failed", "fail_reason": str(error),
                    "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }, lock)
                pending.pop(unit, None)
                print(counter_line(), flush=True)
                continue
            if not (asset_id and data.get("hasContent")):
                print(
                    f"STATUS unit={unit} render={data.get('renderTaskStatus')} draft={data.get('draftTaskStatus')}",
                    flush=True,
                )
                continue

            target = output_dir / f"{unit}单元{args.output_suffix}.mp4"
            if target.exists() and target.stat().st_size:
                print(f"EXISTS unit={unit} path={target}", flush=True)
            else:
                body = {"out": str(target), "allow_preview": False, "timeout": args.download_timeout}
                if unit_account:
                    body["account"] = unit_account
                if unit_main_account:
                    body["main_account"] = unit_main_account
                try:
                    result = api_json(
                        f"{base_url}/v1/assets/{asset_id}/download",
                        method="POST", payload=body, timeout=args.download_timeout + 30,
                    )
                except ProxyError as exc:
                    print(f"DOWNLOAD_ERROR unit={unit} {exc}", file=sys.stderr, flush=True)
                    continue
                written = (result.get("data") or [{}])[0]
                if not target.is_file() or not target.stat().st_size:
                    print(f"DOWNLOAD_ERROR unit={unit} 代理报成功但目标文件为空: {written}", file=sys.stderr, flush=True)
                    continue
                print(f"DOWNLOADED unit={unit} bytes={target.stat().st_size}", flush=True)

            resolution = ffprobe_resolution(ffprobe, target)
            if resolution is None:
                print(f"WARN unit={unit} ffprobe 不可用，未核验分辨率（symphony配置.json 的 ffprobe 路径）", flush=True)
            elif resolution in {"360x640"}:
                final_errors[unit] = f"下到的是播放转码流 {resolution}，不是渲染原片"
                print(f"LOWRES unit={unit} {resolution}", file=sys.stderr, flush=True)
            else:
                print(f"RESOLUTION unit={unit} {resolution}", flush=True)

            append_ledger(ledger, {
                "platform": PLATFORM, "unit": unit, "task_id": task_id, "asset_id": asset_id,
                "story_root": story_scope,
                "main_account": unit_main_account, "account": unit_account,
                "status": "succeeded", "output_path": str(target),
                "resolution": resolution, "bytes": target.stat().st_size,
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }, lock)
            pending.pop(unit, None)
            print(counter_line(), flush=True)
        if pending:
            time.sleep(args.interval)

    for unit in pending:
        final_errors[unit] = "轮询超时"
    print_account_breakdown()
    if final_errors:
        for unit, reason in sorted(final_errors.items()):
            print(f"FINAL_ERROR unit={unit} reason={reason}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(description="TikTok Symphony 视频单元提交/轮询")
    parser.add_argument("mode", choices=["preflight", "warmup", "submit", "poll", "stats"])
    parser.add_argument("--story-root", default=None,
                        help="故事目录；stats 模式不需要，其余模式必填")
    parser.add_argument("--prompt-file", default="分镜提示词.md",
                        help="相对故事目录的分镜提示词路径，如 分镜-15秒/分镜提示词.md")
    parser.add_argument("--units", default="", help="如 1,3,5-8")
    parser.add_argument("--model", default=None, help="完整模型名，必须按当前批次与用户确认")
    parser.add_argument("--ledger", default=None, help="工具/运行垃圾/tasks/{集号}/ 下的 jsonl 账本")
    parser.add_argument(
        "--main-account",
        default=None,
        help="固定单个 TK 主账号别名（main-a 或 main-b）；留空用 symphony配置.json 的默认主账号",
    )
    parser.add_argument("--main-accounts", default=None, metavar="all|别名1,别名2",
                        help="跨主账号轮转：填 all 用全部已登录主账号，或逗号分隔指定。"
                             "给了这个参数即进入轮转模式，--accounts 省略等同 all")
    parser.add_argument("--account", default=None, help="固定单个子账号；留空用 symphony配置.json 的默认账号")
    parser.add_argument("--accounts", default=None, metavar="all|名1,名2",
                        help="跨子账号轮转：填 all 用全部子账号，或逗号分隔指定（如 main-a,main-b）。"
                             "每子账号 5 条并发＋独立积分，池并发上限＝子账号数×5")
    parser.add_argument("--backend", default="auto", choices=["auto", "direct", "browser"])
    parser.add_argument("--confirm-credits", type=int, default=None,
                        help="本批总积分，必须等于 preflight 报的数字才放行提交")
    parser.add_argument("--round-duration-up", action="store_true",
                        help="把非 5/10/12/15 的时长向上取整（会多扣积分且成片比脚本长）")
    parser.add_argument("--output-suffix", default="-symphony")
    parser.add_argument("--interval", type=int, default=300,
                        help="轮询/等槽位间隔秒数；默认 5 分钟（防风控，"
                             "高频轮询有封号风险，不得调回 20 秒级）")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--submit-timeout", type=int, default=600,
                        help="单条提交超时；backend=auto 遇到未登记的新参考图会先开后台 Chrome 上传，较慢")
    parser.add_argument("--download-timeout", type=int, default=240)
    parser.add_argument("--throttle", action="store_true",
                        help=f"兼容旧命令的保留参数；Symphony 始终强制限流，单账号硬上限 {MAX_INFLIGHT} 条")
    parser.add_argument("--max-inflight", type=int, default=MAX_INFLIGHT,
                        help=f"单账号在渲上限；无论传入多大都不会超过平台硬上限 {MAX_INFLIGHT}")
    parser.add_argument("--resume", action="store_true",
                        help="续跑同一个账本：跳过已提交/已完成的单元，只补剩下的")
    parser.add_argument("--watch", action="store_true",
                        help="submit 专用：提交完成后自动进入 poll，一条命令跑完提交→轮询→下载；"
                             "unknown_outcome 停批时不进入轮询，等人工核实")
    parser.add_argument("--warmup-timeout", type=int, default=900,
                        help="单批热预存超时；上传是最慢的一段，图越大越久")
    parser.add_argument("--warmup-current", action="store_true",
                        help="warmup 复用主账号 profile 当前 workspace，不执行网页切号")
    parser.add_argument("--allow-partial", action="store_true",
                        help="绕过 fenjing_ready 检查，仅用户明确点名放行部分单元时使用")
    args = parser.parse_args()

    if args.mode == "stats":
        # 纯本地扫账本，不连代理：随时可查今日提交/成功/被拒/卡人脸与各子账号提交数
        print_account_breakdown()
        attr = slot_attribution()
        if attr["foreign"]:
            rows = "、".join(
                f"{label} {n} 条"
                for label, n in sorted(attr["foreign"].items(), key=lambda kv: (-kv[1], kv[0]))
            )
            print(f"当前在渲占槽（按账本，近30分钟提交未出结果）：{rows}", flush=True)
        else:
            print("当前无账本在渲任务", flush=True)
        if attr["recent_failed"]:
            print(
                f"另有 {attr['recent_failed']} 条近5分钟刚失败，槽位待平台释放（延迟约2分钟）",
                flush=True,
            )
        return 0
    if not args.story_root:
        parser.error(f"{args.mode} 需要 --story-root")
    if args.mode in {"preflight", "warmup", "submit"} and not args.units:
        parser.error(f"{args.mode} 需要 --units")
    if args.mode == "submit":
        if not args.model:
            parser.error("submit 需要 --model，模型必须按当前批次与用户确认")
        if not args.ledger:
            parser.error("submit 需要 --ledger")
        if args.confirm_credits is None:
            parser.error("submit 需要 --confirm-credits，数字取自 preflight 的 BUDGET 行")
    if args.mode == "poll" and not args.ledger:
        parser.error("poll 需要 --ledger")

    if args.mode == "preflight":
        return preflight(args)
    if args.mode == "warmup":
        return warmup(args)
    if args.mode == "submit":
        rc = submit(args)
        if not args.watch:
            return rc
        if rc == 2:
            # unknown_outcome：可能已扣费的悬案必须先人工核实，且停批提示要留在输出末尾，
            # 不能被轮询日志冲掉。已提交单元照常在渲，之后手动 poll 收片即可。
            print("WATCH 停批（unknown_outcome），未进入轮询——先人工核实平台任务列表", file=sys.stderr, flush=True)
            return rc
        ledger = Path(args.ledger).resolve()
        if not ledger.exists() or not ledger.stat().st_size:
            print("WATCH 账本为空（没有任何单元提交成功），跳过轮询", flush=True)
            return rc
        print(f"WATCH submit 结束（exit={rc}），进入轮询下载", flush=True)
        return max(rc, poll(args))
    return poll(args)


if __name__ == "__main__":
    raise SystemExit(main())
