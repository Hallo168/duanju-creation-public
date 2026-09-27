from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings
from .direct_client import (
    MODEL_IDS,
    AssetCache,
    DirectOutcomeUnknown,
    DirectRejected,
    DirectUnavailable,
    SymphonyDirectClient,
)
from .licensing import LicenseError, LicenseManager, machine_code
from .models import (
    DownloadRequest,
    FlowImageDownloadRequest,
    FlowImageGenerationRequest,
    LicenseActivationRequest,
    VideoGenerationRequest,
    WarmupRequest,
)
from .runner import CLITimeout, Runner, SubprocessRunner
from .session_store import list_main_accounts

logger = logging.getLogger("tiktok_symphony_proxy")
DEFAULT_TIMEOUT = 30
MAX_ACCOUNT_INFLIGHT = 5


RELEASE_GRACE_SECONDS = 10 * 60  # 已核实终结的任务，等平台把它清出计数前的观察期
UNKNOWN_OUTCOME_TTL = 15 * 60  # outcome-unknown 占位保留时长（无 task_id 无法核实）
STALE_TASK_TTL = 24 * 3600  # 登记超过一天还没终结＝登记失效，防泄漏


class AccountCapacityGate:
    """Fail-closed per-account submission gate, task-truth based (V35 根治).

    平台的 generating-task-count 会把已终结但未清理的任务继续算在内（实测
    2026-08-04：账本仅能解释约 17 条时它报 75，两分钟后骤降到 7），谁信它谁就
    在"假满"上空等。根治：代理登记自己提交的每条 task_id，判满前逐条核实真实
    状态，只有核实后仍满才算满；平台计数只用来兜底代理不知道的外部任务
    （external，保守当作在渲——fail-closed，不会超投）。登记只在内存：代理重启
    后退化为全信平台计数，行为等同旧版，几分钟内随平台清理自愈。
    """

    def __init__(self, limit: int = MAX_ACCOUNT_INFLIGHT) -> None:
        self.limit = limit
        self._meta_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._states: dict[str, dict[str, dict]] = {}

    @contextmanager
    def account(self, account_id: str) -> Iterator[dict[str, dict]]:
        with self._meta_lock:
            lock = self._locks.setdefault(account_id, threading.Lock())
            state = self._states.setdefault(account_id, {})
        with lock:
            yield state

    @staticmethod
    def _prune(state: dict[str, dict], now: float) -> None:
        for tid in list(state):
            entry = state[tid]
            terminal_at = entry.get("terminal_at")
            if terminal_at is not None:
                if now - float(terminal_at) > RELEASE_GRACE_SECONDS:
                    del state[tid]
            elif entry.get("unverifiable"):
                if now - float(entry.get("submitted_at") or now) > UNKNOWN_OUTCOME_TTL:
                    del state[tid]
            elif now - float(entry.get("submitted_at") or now) > STALE_TASK_TTL:
                del state[tid]

    def occupancy(self, state: dict[str, dict], remote: int) -> dict[str, int]:
        """active＝登记在案且未终结；external＝平台计数里登记解释不了的部分（外部
        任务，保守占槽）；已核实终结但平台可能还没清掉的（recent_terminal）不占槽
        也不让平台计数重复算进 external。"""
        now = time.time()
        self._prune(state, now)
        active = sum(1 for entry in state.values() if entry.get("terminal_at") is None)
        recent_terminal = len(state) - active
        external = max(0, int(remote) - active - recent_terminal)
        return {
            "active": active,
            "recent_terminal": recent_terminal,
            "external": external,
            "effective": active + external,
        }

    @staticmethod
    def register(state: dict[str, dict], task_id: Any) -> None:
        """提交成功后登记；拿不到 task_id（outcome-unknown）登记一个不可核实的
        占位，按 TTL 过期。"""
        if task_id:
            state[str(task_id)] = {"submitted_at": time.time()}
        else:
            state[f"unknown-{time.time()}"] = {
                "submitted_at": time.time(),
                "unverifiable": True,
            }

    @staticmethod
    def verify_active(state: dict[str, dict], is_terminal: Callable[[str], bool]) -> int:
        """逐条核实未终结的登记任务，返回本轮新判定终结的条数。
        is_terminal 抛错按仍在渲处理（fail-closed）。"""
        resolved = 0
        for tid, entry in list(state.items()):
            if entry.get("terminal_at") is not None or entry.get("unverifiable"):
                continue
            try:
                terminal = bool(is_terminal(tid))
            except Exception:  # noqa: BLE001 —— 查询失败一律保守当在渲
                continue
            if terminal:
                entry["terminal_at"] = time.time()
                resolved += 1
        return resolved


def _task_is_terminal(direct: SymphonyDirectClient, task_id: str) -> bool:
    """与批量执行器 poll 同一套终态语义：报错＝失败终结；有成片＝成功终结。"""
    draft = direct.check_task(task_id)
    if draft.get("generateErrorMessage") or draft.get("failReason") or draft.get("errorMsg"):
        return True
    return bool(draft.get("hasContent") and (draft.get("vid") or draft.get("watermarkVid")))

# 静态模型表：给 工具/video_provider_catalog.py 做零成本探活用（硬性规则 15 要求每批
# 实时回显平台与模型，不得沿用上批模型名）。平台真实控件随改版会变，权威来源仍是
# `tiktok-symphony composer-options --mode reference`，但那条要开浏览器，不适合每批跑。
# 改版后请同步这张表。
MODEL_CATALOG = [
    {
        "id": "Dreamina Seedance 2.0",
        "name": "Dreamina Seedance 2.0",
        "recommended": True,
        "note": "短剧一律用它——能按提示词切分镜。Video 1.5 Pro 会揉成一个静止长镜头并丢主体。",
        "durations": [5, 10, 12, 15],
        "max_refs": 9,
        "credits_per_second": 1,
        "render_minutes_at_15s": 6,
        "aspect": "9:16",
        "resolutions": ["720x1280", "1080x1920"],
        "direct": True,
    },
    {
        "id": "Video 1.5 Pro",
        "name": "Video 1.5 Pro",
        "recommended": False,
        "note": "单镜头模型，短剧不用。只能走浏览器后端（backend=browser）。",
        "durations": [5, 10, 12, 15],
        "max_refs": 9,
        "credits_per_second": 1,
        "aspect": "9:16",
        "direct": False,
    },
]

def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"ok": False, "error": error})

def _cached_digests(asset_cache: AssetCache) -> set[str]:
    """资产缓存当前登记了哪些文件（按内容 SHA-256）。缺文件或坏 JSON 都当作空。"""
    try:
        raw = json.loads(asset_cache.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {key for key, value in raw.items() if isinstance(value, dict) and value.get("url")}


def _direct_session_state(make_direct: Callable[[], SymphonyDirectClient]) -> dict[str, Any]:
    """登录态是否存在。只构造客户端不发请求，所以零网络开销、零费用。

    存在不等于没过期——真过期要等第一次请求回 401/403 才知道。
    """
    direct = None
    try:
        direct = make_direct()
        return {"ready": True}
    except DirectUnavailable as exc:
        return {"ready": False, "reason": str(exc)}
    finally:
        if direct is not None:
            direct.close()


def _licensed_feature(request: Request) -> str:
    path = request.url.path
    if path in {"/v1/videos/generations", "/v1/images/generations"}:
        return "generate"
    if path.startswith("/v1/images/") and path.endswith("/download"):
        return "download"
    if path.startswith("/v1/assets/") and path.endswith("/download"):
        return "download"
    if path.startswith("/v1/direct/accounts"):
        return "accounts"
    return "core"

def create_app(
    runner: Runner | None = None,
    node_path: str | None = None,
    cli_path: str | None = None,
    direct_factory: Callable[..., SymphonyDirectClient] | None = None,
    license_manager: LicenseManager | None = None,
) -> FastAPI:
    settings = Settings.from_env()
    cli_runner = runner or SubprocessRunner()
    cli_base = [node_path or settings.node_path, cli_path or settings.cli_path]
    def make_direct(main_account: str | None = None) -> SymphonyDirectClient:
        if direct_factory is None:
            return SymphonyDirectClient(main_account=main_account)
        if main_account is None:
            return direct_factory()
        return direct_factory(main_account)

    def browser_profile_for(main_account: str | None) -> str | None:
        if not main_account:
            return None
        for item in list_main_accounts():
            if item.get("main_account") == main_account:
                return str(item.get("browser_profile") or main_account)
        return main_account
    asset_cache = AssetCache(Path(settings.asset_cache_path))
    capacity_gate = AccountCapacityGate()
    # 许可证强制校验默认关闭（2026-08-02 用户裁定：个人使用不再需要防拷贝锁）。
    # 显式传入 license_manager（测试/受保护构建）或设 SYMPHONY_LICENSE_ENFORCE=1 时才拦截；
    # 端点与签发工具全部保留，恢复分发时打开开关即可。
    license_enforced = license_manager is not None or (
        os.getenv("SYMPHONY_LICENSE_ENFORCE", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    license_setup_error: LicenseError | None = None
    if license_manager is None:
        try:
            license_manager = LicenseManager.from_public_key_file(
                Path(settings.license_public_key_path)
            )
        except LicenseError as exc:
            license_setup_error = exc
    api = FastAPI(title="TikTok Symphony Proxy", version="0.1.0")

    public_paths = {
        "/health",
        "/v1/models",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/v1/license/machine",
        "/v1/license/status",
        "/v1/license/activate",
    }

    @api.middleware("http")
    async def enforce_license(request: Request, call_next: Callable[..., Any]) -> JSONResponse:
        if license_enforced and request.url.path not in public_paths:
            if license_setup_error is not None:
                return _error(503, license_setup_error.code, str(license_setup_error))
            try:
                feature = _licensed_feature(request)
                assert license_manager is not None
                license_manager.verify_installed(feature=feature)
            except LicenseError as exc:
                return _error(403, exc.code, str(exc))
        return await call_next(request)

    @api.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{k: v for k, v in item.items() if k not in {"input", "ctx"}} for item in exc.errors()]
        return _error(422, "validation_error", "Request validation failed", details)

    def invoke(
        args: list[str],
        timeout: int = DEFAULT_TIMEOUT,
        *,
        empty_ok: bool = False,
        site: str = "tiktok-symphony",
        browser_profile: str | None = None,
    ) -> JSONResponse:
        if settings.direct_only:
            return _error(
                503,
                "browser_helper_unavailable",
                "This protected build only includes the direct backend",
            )
        profile_args = ["--profile", browser_profile] if browser_profile else []
        argv = [*cli_base, *profile_args, site, *args, "-f", "json"]
        try:
            result = cli_runner.run(argv, timeout)
        except CLITimeout as exc:
            logger.error("OpenCLI timed out after %s seconds", exc.timeout)
            return _error(502, "cli_timeout", "OpenCLI command timed out", {"timeout": exc.timeout})
        except OSError as exc:
            logger.error("Could not start OpenCLI: %s", exc)
            return _error(502, "cli_unavailable", "Could not start OpenCLI", {"reason": str(exc)})
        if result.exit_code == 66 and empty_ok:
            return JSONResponse(content={"ok": True, "data": []})
        if result.exit_code != 0:
            return _error(502, "cli_error", "OpenCLI command failed", {
                "exit_code": result.exit_code,
                "stderr": result.stderr.strip(),
            })
        output = result.stdout.strip()
        try:
            data = json.loads(output) if output else None
        except json.JSONDecodeError:
            data = {"output": output}
        return JSONResponse(content={"ok": True, "data": data})

    @api.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "data": {"status": "healthy"}}

    @api.get("/v1/models")
    def models() -> JSONResponse:
        """探活端点：零成本、不开浏览器、不扣积分，也不需要许可证。

        除模型表外一并回显许可证与直连登录态，让 video_provider_catalog.py 的一次
        探活就能判断这个通道当前能不能提交。
        """
        if not license_enforced:
            license_state: dict[str, Any] = {"valid": True, "code": "not_enforced", "edition": "personal", "expiresAt": None}
        elif license_manager is None:
            license_state = {"valid": False, "code": "license_not_configured"}
        else:
            status = license_manager.status()
            license_state = {k: status.get(k) for k in ("valid", "code", "edition", "expiresAt")}
        if license_enforced and license_setup_error is not None:
            license_state = {"valid": False, "code": license_setup_error.code}
        return JSONResponse(content={"ok": True, "data": {
            "provider": "tiktok_symphony",
            "source": "static",
            "models": [{**m, "model_id": MODEL_IDS.get(m["id"])} for m in MODEL_CATALOG],
            "license": license_state,
            "direct_session": _direct_session_state(make_direct),
            "main_accounts": list_main_accounts(),
        }})

    @api.get("/v1/license/machine")
    def license_machine() -> JSONResponse:
        try:
            return JSONResponse(content={"ok": True, "data": {
                "machineCode": machine_code(),
            }})
        except LicenseError as exc:
            return _error(503, exc.code, str(exc))

    @api.get("/v1/license/status")
    def license_status() -> JSONResponse:
        # 许可证强制校验默认关闭（2026-08-02 用户裁定：个人使用不再需要防拷贝锁）。
        # 与 /v1/models 的探活口径保持一致：未强制时一律报 valid，不受底层许可证
        # 过期/未激活影响——2026-08-05 用户（源码作者）裁定移除许可证门槛。
        if not license_enforced:
            return JSONResponse(content={"ok": True, "data": {
                "valid": True, "code": "not_enforced", "edition": "personal", "expiresAt": None,
                "machineCode": machine_code(), "productVersion": "0.1.0",
            }})
        if license_setup_error is not None:
            return _error(503, license_setup_error.code, str(license_setup_error))
        assert license_manager is not None
        return JSONResponse(content={"ok": True, "data": license_manager.status()})

    @api.post("/v1/license/activate")
    def activate_license(body: LicenseActivationRequest) -> JSONResponse:
        if license_setup_error is not None:
            return _error(503, license_setup_error.code, str(license_setup_error))
        try:
            assert license_manager is not None
            verified = license_manager.activate(body.license)
            return JSONResponse(content={"ok": True, "data": verified.public_status()})
        except LicenseError as exc:
            return _error(403, exc.code, str(exc))

    @api.get("/v1/credits")
    def credits(main_account: str | None = None) -> JSONResponse:
        return invoke(
            ["credits", "--window", "background", "--site-session", "persistent"],
            browser_profile=browser_profile_for(main_account),
        )

    @api.get("/v1/jobs")
    def jobs(
        limit: int = Query(default=20, ge=1, le=50),
        pending: bool = Query(default=False),
        main_account: str | None = None,
    ) -> JSONResponse:
        return invoke(
            ["jobs", "--limit", str(limit), "--pending", str(pending).lower()],
            empty_ok=True,
            browser_profile=browser_profile_for(main_account),
        )

    @api.get("/v1/generations")
    def generations(
        limit: int = Query(default=20, ge=1, le=50),
        main_account: str | None = None,
    ) -> JSONResponse:
        return invoke(
            ["generations", "--limit", str(limit)],
            empty_ok=True,
            browser_profile=browser_profile_for(main_account),
        )

    @api.get("/v1/direct/main-accounts")
    def direct_main_accounts() -> JSONResponse:
        return JSONResponse(content={"ok": True, "data": list_main_accounts()})

    @api.get("/v1/direct/accounts")
    def direct_accounts(main_account: str | None = None) -> JSONResponse:
        direct = None
        try:
            direct = make_direct(main_account)
            rows = (
                direct.list_accounts(persist=True)
                if main_account
                else direct.list_accounts()
            )
            data: Any = rows if main_account is None else {
                "mainAccount": main_account,
                "accounts": rows,
            }
            return JSONResponse(content={"ok": True, "data": data})
        except DirectUnavailable as exc:
            return _error(503, "direct_unavailable", str(exc))
        finally:
            if direct is not None:
                direct.close()

    @api.post("/v1/direct/accounts/{account}/activate")
    def activate_direct_account(account: str, main_account: str | None = None) -> JSONResponse:
        direct = None
        try:
            direct = make_direct(main_account)
            selected = direct.select_account(account, persist=True)
            verified = direct.credits()
            return JSONResponse(content={"ok": True, "data": {
                **selected,
                "credits": verified["credits"],
                "weeklySpent": verified["weeklySpent"],
                "backend": "direct",
                **({"mainAccount": main_account} if main_account else {}),
            }})
        except DirectUnavailable as exc:
            return _error(503, "direct_unavailable", str(exc))
        finally:
            if direct is not None:
                direct.close()

    @api.get("/v1/direct/credits")
    def direct_credits(
        account: str | None = None,
        main_account: str | None = None,
    ) -> JSONResponse:
        """余额查询的直连版本：不开浏览器，供提交前的积分预算护栏用。

        /v1/credits 走 OpenCLI，会起一次后台 Chrome；批量提交前只想核对余额时用这个。
        """
        direct = None
        try:
            direct = make_direct(main_account)
            if account:
                direct.select_account(account)
            return JSONResponse(content={"ok": True, "data": {
                **direct.credits(),
                "backend": "direct",
                **({"mainAccount": main_account} if main_account else {}),
            }})
        except DirectUnavailable as exc:
            return _error(503, "direct_unavailable", str(exc))
        finally:
            if direct is not None:
                direct.close()

    @api.get("/v1/direct/pool")
    def direct_pool(
        accounts: list[str] | None = Query(default=None),
        main_account: str | None = None,
    ) -> JSONResponse:
        """全部子账号的余额与在渲条数，一次拿齐——跨账号轮转调度的判据。

        每个子账号的积分与并发额度都独立（5 条/账号），所以 5 个账号理论上限 25 条。
        每个账号用独立客户端并发查询，避免共享可变账号 Cookie。单个账号查不动时
        只标记该账号 error，不影响其余账号继续调度。accounts 可重复传参或使用逗号分隔。
        """
        direct = None
        try:
            direct = make_direct(main_account)
            directory = direct.account_directory()
            direct.close()
            direct = None

            requested: list[str] = []
            for value in accounts or []:
                requested.extend(part.strip() for part in value.split(",") if part.strip())
            if requested:
                known = {
                    value
                    for account in directory
                    for value in (account["name"], account["id"])
                }
                unknown = list(dict.fromkeys(value for value in requested if value not in known))
                if unknown:
                    return _error(
                        422,
                        "unknown_account",
                        "One or more Symphony accounts do not exist",
                        {"accounts": unknown},
                    )
                wanted = set(requested)
                directory = [
                    account
                    for account in directory
                    if account["name"] in wanted or account["id"] in wanted
                ]

            def inspect(account: dict[str, str]) -> dict[str, Any]:
                worker = None
                try:
                    worker = make_direct(main_account)
                    selected = worker.select_known_account(account)
                    credits = worker.credits_for_verified_account(selected)
                    remote = worker.generating_count()
                    # generating 回报的是**核实后的真实占用**（登记任务＋外部兜底），
                    # 不再直接转述平台计数：平台计数把已终结未清理的任务也算在内，
                    # 直接转述会让调度器在"假满"上空等（V35）。显示满时先逐条核实。
                    capacity_key = f"{main_account or 'legacy'}:{selected['id']}"
                    with capacity_gate.account(capacity_key) as capacity:
                        occ = capacity_gate.occupancy(capacity, remote)
                        if occ["effective"] >= MAX_ACCOUNT_INFLIGHT and occ["active"]:
                            capacity_gate.verify_active(
                                capacity, lambda tid: _task_is_terminal(worker, tid)
                            )
                            occ = capacity_gate.occupancy(capacity, remote)
                    return {
                        "name": selected["name"],
                        **({"mainAccount": main_account} if main_account else {}),
                        "credits": credits["credits"],
                        "generating": occ["effective"],
                        "reserved": 0,
                        "platformCount": remote,
                        "tracked": occ["active"],
                        "external": occ["external"],
                        "effective": occ["effective"],
                        "limit": MAX_ACCOUNT_INFLIGHT,
                    }
                except DirectUnavailable as exc:
                    return {"name": account["name"], "error": str(exc)}
                finally:
                    if worker is not None:
                        worker.close()

            with ThreadPoolExecutor(max_workers=min(5, len(directory) or 1)) as executor:
                rows = list(executor.map(inspect, directory))
            return JSONResponse(content={"ok": True, "data": rows})
        except DirectUnavailable as exc:
            return _error(503, "direct_unavailable", str(exc))
        finally:
            if direct is not None:
                direct.close()

    @api.get("/v1/direct/generating-count")
    def direct_generating_count(
        account: str | None = None,
        main_account: str | None = None,
    ) -> JSONResponse:
        """当前账号有几条还在渲染。**单子账号并发上限 5 条**，批量提交必须按这个节流。"""
        direct = None
        try:
            direct = make_direct(main_account)
            selected = direct.select_account(account) if account else direct.current_account()
            remote = direct.generating_count()
            # 与 /v1/direct/pool 同口径：generating＝核实后的真实占用（V35），
            # 显示满时先逐条核实登记任务，避免 wait_for_slot 在僵尸计数上空等。
            capacity_key = f"{main_account or 'legacy'}:{selected['id']}"
            with capacity_gate.account(capacity_key) as capacity:
                occ = capacity_gate.occupancy(capacity, remote)
                if occ["effective"] >= MAX_ACCOUNT_INFLIGHT and occ["active"]:
                    capacity_gate.verify_active(
                        capacity, lambda tid: _task_is_terminal(direct, tid)
                    )
                    occ = capacity_gate.occupancy(capacity, remote)
            return JSONResponse(content={"ok": True, "data": {
                "account": selected["name"],
                **({"mainAccount": main_account} if main_account else {}),
                "generating": occ["effective"],
                "reserved": 0,
                "platformCount": remote,
                "tracked": occ["active"],
                "external": occ["external"],
                "effective": occ["effective"],
                "limit": MAX_ACCOUNT_INFLIGHT,
            }})
        except DirectUnavailable as exc:
            return _error(503, "direct_unavailable", str(exc))
        finally:
            if direct is not None:
                direct.close()

    @api.get("/v1/direct/tasks/{task_id}")
    def direct_task(
        task_id: str,
        account: str | None = None,
        main_account: str | None = None,
        raw: bool = False,
    ) -> JSONResponse:
        direct = None
        try:
            direct = make_direct(main_account)
            selected = direct.select_account(account) if account else direct.current_account()
            draft = direct.check_task(task_id)
            if raw:
                # 状态枚举没有公开文档，只靠挑出来的几个字段判生死会误判。
                # raw=true 直接吐原始 draft，用来对照真实的成功/失败样本。
                return JSONResponse(content={"ok": True, "data": draft})
            return JSONResponse(content={"ok": True, "data": {
                "taskId": task_id,
                "account": selected["name"],
                **({"mainAccount": main_account} if main_account else {}),
                "renderTaskStatus": draft.get("renderTaskStatus"),
                "draftTaskStatus": draft.get("draftTaskStatus"),
                "assetId": draft.get("vid") or draft.get("watermarkVid") or None,
                "hasContent": bool(draft.get("hasContent")),
                "error": (
                    draft.get("generateErrorMessage")
                    or draft.get("failReason")
                    or draft.get("errorMsg")
                    or None
                ),
            }})
        except DirectUnavailable as exc:
            return _error(503, "direct_unavailable", str(exc))
        finally:
            if direct is not None:
                direct.close()

    @api.post("/v1/images/generations")
    def generate_flow_image(body: FlowImageGenerationRequest) -> JSONResponse:
        if body.reference_path and not Path(body.reference_path).is_file():
            return _error(
                422,
                "validation_error",
                "Reference file does not exist",
                {"reference_path": body.reference_path},
            )
        args = [
            "generate-image",
            body.prompt,
            "--project",
            body.project_id,
            "--wait",
            str(body.wait).lower(),
            "--prepare-only",
            str(body.prepare_only).lower(),
            "--timeout",
            str(body.timeout),
            "--window",
            "background",
            "--site-session",
            "persistent",
        ]
        if body.reference_path:
            args[2:2] = ["--ref", body.reference_path]
        else:
            args[2:2] = ["--reference", str(body.uploaded_reference)]

        generated = invoke(args, body.timeout + 60, site="google-flow")
        if generated.status_code != 200 or body.out is None:
            return generated

        generated_payload = json.loads(bytes(generated.body).decode("utf-8"))
        rows = generated_payload.get("data")
        row = rows[0] if isinstance(rows, list) and rows else rows
        asset_id = row.get("assetId") if isinstance(row, dict) else None
        if not asset_id:
            return _error(
                502,
                "flow_asset_missing",
                "Flow accepted the generation but did not return a downloadable asset",
                {"generation": rows},
            )
        downloaded = invoke(
            [
                "download-image",
                asset_id,
                "--project",
                body.project_id,
                "--out",
                body.out,
                "--timeout",
                str(min(body.timeout, 3600)),
                "--window",
                "background",
                "--site-session",
                "persistent",
            ],
            min(body.timeout, 3600) + 30,
            site="google-flow",
        )
        if downloaded.status_code != 200:
            return downloaded
        downloaded_payload = json.loads(bytes(downloaded.body).decode("utf-8"))
        return JSONResponse(content={"ok": True, "data": {
            "generation": row,
            "download": downloaded_payload.get("data"),
        }})

    @api.post("/v1/images/{asset_id}/download")
    def download_flow_image(asset_id: str, body: FlowImageDownloadRequest) -> JSONResponse:
        return invoke(
            [
                "download-image",
                asset_id,
                "--project",
                body.project_id,
                "--out",
                body.out,
                "--timeout",
                str(body.timeout),
                "--window",
                "background",
                "--site-session",
                "persistent",
            ],
            body.timeout + 30,
            site="google-flow",
        )

    @api.post("/v1/videos/generations")
    def generate_video(body: VideoGenerationRequest) -> JSONResponse:
        missing = [ref for ref in body.refs if not Path(ref).is_file()]
        if missing:
            return _error(422, "validation_error", "Reference file does not exist", {"refs": missing})
        direct_supported = (
            body.mode in {"auto", "reference"}
            and len(body.refs) >= 1
            and body.model in {None, "Dreamina Seedance 2.0"}
            and not body.wait
        )
        if body.account and (body.backend == "browser" or not direct_supported):
            return _error(
                422,
                "direct_account_required",
                "Explicit account selection requires direct, non-waiting reference generation with Seedance 2.0",
            )
        if body.backend != "browser" and direct_supported:
            direct = None
            try:
                direct = make_direct(body.main_account)
                selected = direct.select_account(body.account) if body.account else direct.current_account()
                cached = asset_cache.resolve(body.refs)
                capacity_key = f"{body.main_account or 'legacy'}:{selected['id']}"
                with capacity_gate.account(capacity_key) as capacity:
                    remote = direct.generating_count()
                    occ = capacity_gate.occupancy(capacity, remote)
                    if occ["effective"] >= MAX_ACCOUNT_INFLIGHT:
                        # 平台计数会把已终结未清理的任务算在内——判满前逐条核实
                        # 登记任务的真实状态，核实后仍满才拒（V35 根治假满空等）
                        capacity_gate.verify_active(
                            capacity, lambda tid: _task_is_terminal(direct, tid)
                        )
                        remote = direct.generating_count()
                        occ = capacity_gate.occupancy(capacity, remote)
                    if occ["effective"] >= MAX_ACCOUNT_INFLIGHT:
                        return _error(
                            429,
                            "account_capacity_exhausted",
                            "This Symphony sub-account already has 5 verified active generations",
                            {
                                "account": selected["name"],
                                **({"mainAccount": body.main_account} if body.main_account else {}),
                                "generating": occ["effective"],
                                "reserved": 0,
                                "platformCount": remote,
                                "tracked": occ["active"],
                                "external": occ["external"],
                                "effective": occ["effective"],
                                "limit": MAX_ACCOUNT_INFLIGHT,
                            },
                        )
                    try:
                        result = direct.generate_reference_video(
                            prompt=body.prompt,
                            assets=cached,
                            duration=body.duration,
                            model=body.model or "Dreamina Seedance 2.0",
                        )
                    except DirectOutcomeUnknown:
                        # It may already exist and be billable, so keep a slot.
                        capacity_gate.register(capacity, None)
                        raise
                    capacity_gate.register(
                        capacity, result.get("data", {}).get("task_id")
                    )
                return JSONResponse(content={"ok": True, "data": [{
                    "status": "generating",
                    "taskId": result.get("data", {}).get("task_id"),
                    "backend": "direct",
                    **({"mainAccount": body.main_account} if body.main_account else {}),
                    "account": selected["name"],
                    "mode": "reference",
                    "model": body.model or "Dreamina Seedance 2.0",
                    "duration": f"{body.duration}s",
                    "prompt": body.prompt,
                }]})
            except DirectUnavailable as exc:
                if body.backend == "direct" or body.account:
                    return _error(503, "direct_unavailable", str(exc))
                logger.warning("Direct Symphony path unavailable before submission; using browser fallback: %s", exc)
            except DirectRejected as exc:
                # 平台明确拒绝（如 10001303 内容审核）：没建任务、没扣分，是确定性结果。
                # 与 direct_outcome_unknown 区分开，调用方可以只跳过这一条继续跑整批。
                return _error(
                    422,
                    "direct_rejected",
                    "Symphony rejected this submission; nothing was created or charged",
                    {"platform_code": exc.code, "platform_message": exc.message},
                )
            except DirectOutcomeUnknown as exc:
                return _error(
                    502,
                    "direct_outcome_unknown",
                    "Direct submission outcome is unknown; browser retry was suppressed to avoid duplicate billing",
                    {"reason": str(exc)},
                )
            finally:
                if direct is not None:
                    direct.close()
        elif body.backend == "direct":
            return _error(
                422,
                "direct_unsupported",
                "Direct mode currently supports non-waiting reference video generation with Seedance 2.0",
            )
        args = [
            "generate-video", body.prompt,
            "--mode", body.mode,
            "--frames", body.frames,
            "--duration", str(body.duration),
            "--wait", str(body.wait).lower(),
            "--timeout", str(body.timeout),
            "--window", "background",
            "--site-session", "persistent",
        ]
        if body.refs:
            args[2:2] = ["--refs", ",".join(body.refs)]
        if body.model is not None:
            args[2:2] = ["--model", body.model]
        return invoke(
            args,
            body.timeout,
            browser_profile=browser_profile_for(body.main_account),
        )

    @api.post("/v1/assets/warmup")
    def warmup(body: WarmupRequest) -> JSONResponse:
        """热预存：把参考图上传登记进资产缓存，让后续提交能纯直连（V22）。

        底层是 OpenCLI 的 `generate-video --prepare-only true`——上传、写缓存、回显 URL，
        但**不点提交按钮**，所以不扣积分（generate-video.js 里 persistReferenceCache 在
        prepare-only 的 return 之前，submitComposer 在其之后）。

        必须经这个端点走，不要在别处直接调 OpenCLI：OpenCLI 的缓存路径默认硬编码指向已停用的
        <本机路径> SYMPHONY_ASSET_CACHE 会由子进程继承从而写对地方。
        """
        missing = [ref for ref in body.refs if not Path(ref).is_file()]
        if missing:
            return _error(422, "validation_error", "Reference file does not exist", {"refs": missing})
        if body.account:
            # 网页端会话落在浏览器 profile 上次活跃的 workspace 上，而日限按子账号算
            # （V29/V31）：落在已限额的子账号里，创作面板报的是「could not switch model
            # to "Dreamina Seedance 2.0"」这种像模型故障的错。所以先用 OpenCLI 的
            # account --use 切到调用方挑好的子账号（自带点击→验证→重试三轮），切不动
            # 就带真实原因失败，不再往下白跑 generate-video。account 命令默认前台窗口
            # ——切号菜单只认真实指针事件，这一步会短暂弹出浏览器窗口。
            switch = invoke(
                ["account", "--use", body.account],
                300,
                browser_profile=browser_profile_for(body.main_account),
            )
            if switch.status_code != 200:
                try:
                    detail = json.loads(bytes(switch.body).decode("utf-8")).get("error")
                except (ValueError, UnicodeDecodeError):
                    detail = None
                # 光靠 uvicorn 的一行裸 502 排障不出来（2026-08-08）：把账号与真实
                # 原因落进代理日志。切号失败的第一嫌疑是该子账号已达当日日限（V31）。
                logger.warning(
                    "Warmup account switch failed for %s/%s: %s —— 首先怀疑账号日限（V31），不是模型故障",
                    body.main_account or "(default)", body.account, detail,
                )
                return _error(
                    502,
                    "warmup_account_switch_failed",
                    f"could not switch the web session to sub-account {body.account!r} before warmup",
                    detail,
                )
        before = _cached_digests(asset_cache)
        args = [
            "generate-video", body.prompt,
            "--mode", "reference",
            "--prepare-only", "true",
            "--refs", ",".join(body.refs),
            "--duration", str(body.duration),
            "--window", "background",
            "--site-session", "persistent",
        ]
        if body.model is not None:
            args[2:2] = ["--model", body.model]
        response = invoke(
            args,
            body.timeout,
            browser_profile=browser_profile_for(body.main_account),
        )
        if response.status_code != 200:
            logger.warning(
                "Warmup CLI failed on %s/%s (HTTP %s) —— 若错误含 could not switch model，"
                "按账号日限处理（V31），不是模型故障",
                body.main_account or "(default)",
                body.account or "(current workspace)",
                response.status_code,
            )
            return response
        after = _cached_digests(asset_cache)
        still_missing = [
            ref for ref in body.refs
            if AssetCache.digest(Path(ref)) not in after
        ]
        payload = json.loads(bytes(response.body).decode("utf-8"))
        return JSONResponse(content={"ok": True, "data": {
            "requested": len(body.refs),
            "newly_cached": len(after - before),
            "already_cached": len(body.refs) - len(still_missing) - len(after - before),
            "still_missing": still_missing,
            "cli": payload.get("data"),
        }})

    @api.post("/v1/assets/{asset_id}/download")
    def download(asset_id: str, body: DownloadRequest) -> JSONResponse:
        if body.account and body.backend == "browser":
            return _error(
                422,
                "direct_account_required",
                "Explicit account selection requires the direct download backend",
            )
        if body.backend != "browser":
            direct = None
            try:
                direct = make_direct(body.main_account)
                selected = direct.select_account(body.account) if body.account else direct.current_account()
                out_dir = Path(body.out) if body.out is not None else Path.cwd()
                file = direct.download_original(asset_id, out_dir)
                return JSONResponse(content={"ok": True, "data": [{
                    "assetId": asset_id,
                    "file": str(file),
                    "bytes": file.stat().st_size,
                    "backend": "direct",
                    **({"mainAccount": body.main_account} if body.main_account else {}),
                    "account": selected["name"],
                }]})
            except DirectUnavailable as exc:
                if body.backend == "direct" or body.account:
                    return _error(503, "direct_unavailable", str(exc))
                logger.warning("Direct Symphony download unavailable; using browser fallback: %s", exc)
            finally:
                if direct is not None:
                    direct.close()
        args = [
            "download", asset_id,
            "--allow-preview", str(body.allow_preview).lower(),
            "--timeout", str(body.timeout),
        ]
        if body.out is not None:
            args.extend(["--out", body.out])
        return invoke(
            args,
            body.timeout + 15,
            browser_profile=browser_profile_for(body.main_account),
        )

    return api

app = create_app()
