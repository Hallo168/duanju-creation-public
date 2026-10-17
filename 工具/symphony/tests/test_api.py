from __future__ import annotations

from pathlib import Path
from typing import Any
import threading
import time
import pytest
from fastapi.testclient import TestClient
from app.direct_client import DirectUnavailable
import app.main as main_module
from app.main import create_app
from app.runner import CLIResult, CLITimeout

class FakeRunner:
    def __init__(self, results: list[CLIResult | Exception] | None = None) -> None:
        self.results = list(results or [CLIResult(0, '{"ok":true}', "")])
        self.calls: list[tuple[list[str], int]] = []
    def run(self, argv: list[str], timeout: int) -> CLIResult:
        self.calls.append((argv, timeout))
        result = self.results.pop(0)
        if isinstance(result, Exception): raise result
        return result

class AllowAllLicense:
    def verify_installed(self, feature: str | None = None) -> None:
        return None

def client_for(runner: FakeRunner) -> TestClient:
    def unavailable_direct():
        raise DirectUnavailable("test direct path disabled")
    return TestClient(create_app(
        runner=runner,
        node_path="node-test",
        cli_path="./cli/main.js",
        direct_factory=unavailable_direct,
        license_manager=AllowAllLicense(),  # type: ignore[arg-type]
    ))

def test_health_does_not_invoke_cli() -> None:
    runner = FakeRunner(); response = client_for(runner).get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": {"status": "healthy"}}
    assert runner.calls == []

def test_direct_only_mode_blocks_browser_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_DIRECT_ONLY", "1")
    runner = FakeRunner()
    response = client_for(runner).get("/v1/jobs")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "browser_helper_unavailable"
    assert runner.calls == []

def test_direct_task_endpoint_normalizes_platform_fields() -> None:
    class FakeDirect:
        closed = False
        def current_account(self) -> dict[str, str]:
            return {"name": "main-b", "id": "account-2"}
        def check_task(self, task_id: str) -> dict[str, Any]:
            assert task_id == "task-1"
            return {
                "renderTaskStatus": 3,
                "draftTaskStatus": 3,
                "watermarkVid": "",
                "generateErrorMessage": "video audit rejected",
            }
        def close(self) -> None:
            self.closed = True

    direct = FakeDirect()
    client = TestClient(create_app(
        runner=FakeRunner(),
        direct_factory=lambda: direct,  # type: ignore[arg-type]
        license_manager=AllowAllLicense(),  # type: ignore[arg-type]
    ))
    response = client.get("/v1/direct/tasks/task-1")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "taskId": "task-1",
        "account": "main-b",
        "renderTaskStatus": 3,
        "draftTaskStatus": 3,
        "assetId": None,
        "hasContent": False,
        "error": "video audit rejected",
    }
    assert direct.closed is True

def test_direct_account_list_endpoint() -> None:
    class FakeDirect:
        closed = False
        def list_accounts(self) -> list[dict[str, Any]]:
            return [
                {"name": "main-a", "id": "account-1", "active": False},
                {"name": "main-b", "id": "account-2", "active": True},
            ]
        def close(self) -> None:
            self.closed = True

    direct = FakeDirect()
    client = TestClient(create_app(
        runner=FakeRunner(),
        direct_factory=lambda: direct,  # type: ignore[arg-type]
        license_manager=AllowAllLicense(),  # type: ignore[arg-type]
    ))
    response = client.get("/v1/direct/accounts")
    assert response.status_code == 200
    assert response.json()["data"][1] == {
        "name": "main-b", "id": "account-2", "active": True
    }
    assert direct.closed is True


def test_named_main_account_routes_to_isolated_direct_factory() -> None:
    seen: list[str] = []

    class FakeDirect:
        def list_accounts(self, *, persist: bool = False) -> list[dict[str, Any]]:
            assert persist is True
            return [{"name": "new-head-10", "id": "account-10", "active": True}]
        def close(self) -> None:
            return None

    def factory(main_account: str) -> FakeDirect:
        seen.append(main_account)
        return FakeDirect()

    client = TestClient(create_app(
        runner=FakeRunner(),
        direct_factory=factory,  # type: ignore[arg-type]
        license_manager=AllowAllLicense(),  # type: ignore[arg-type]
    ))
    response = client.get("/v1/direct/accounts?main_account=main-b")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "mainAccount": "main-b",
        "accounts": [{"name": "new-head-10", "id": "account-10", "active": True}],
    }
    assert seen == ["main-b"]

def test_direct_account_activate_persists_and_verifies_credits() -> None:
    class FakeDirect:
        closed = False
        def select_account(self, account: str, *, persist: bool = False) -> dict[str, str]:
            assert account == "main-a"
            assert persist is True
            return {"name": "main-a", "id": "account-1"}
        def credits(self) -> dict[str, Any]:
            return {"name": "main-a", "id": "account-1", "credits": 1955, "weeklySpent": 45}
        def close(self) -> None:
            self.closed = True

    direct = FakeDirect()
    client = TestClient(create_app(
        runner=FakeRunner(),
        direct_factory=lambda: direct,  # type: ignore[arg-type]
        license_manager=AllowAllLicense(),  # type: ignore[arg-type]
    ))
    response = client.post("/v1/direct/accounts/main-a/activate")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "name": "main-a",
        "id": "account-1",
        "credits": 1955,
        "weeklySpent": 45,
        "backend": "direct",
    }
    assert direct.closed is True


class PoolDirect:
    def __init__(
        self,
        directory: list[dict[str, str]],
        calls: list[tuple[str, str]],
        lock: threading.Lock,
        *,
        coordinator: bool,
        delay: float = 0.0,
        fail_account: str | None = None,
    ) -> None:
        self.directory = directory
        self.calls = calls
        self.lock = lock
        self.coordinator = coordinator
        self.delay = delay
        self.fail_account = fail_account
        self.selected: dict[str, str] | None = None
        self.closed = False

    def _record(self, operation: str, account: str = "") -> None:
        with self.lock:
            self.calls.append((operation, account))
        if self.delay:
            time.sleep(self.delay)

    def account_directory(self) -> list[dict[str, str]]:
        assert self.coordinator
        self._record("directory")
        return [dict(account) for account in self.directory]

    def select_known_account(self, account: dict[str, str]) -> dict[str, str]:
        assert not self.coordinator
        self.selected = dict(account)
        self._record("select", account["name"])
        if account["name"] == self.fail_account:
            raise DirectUnavailable("account temporarily unavailable")
        return dict(account)

    def credits_for_verified_account(self, account: dict[str, str]) -> dict[str, Any]:
        assert account == self.selected
        self._record("credits", account["name"])
        return {**account, "credits": 1000, "weeklySpent": 0}

    def generating_count(self) -> int:
        assert self.selected is not None
        self._record("generating", self.selected["name"])
        return 2

    def close(self) -> None:
        self.closed = True


class PoolDirectFactory:
    def __init__(
        self,
        directory: list[dict[str, str]],
        *,
        delay: float = 0.0,
        fail_account: str | None = None,
    ) -> None:
        self.directory = directory
        self.delay = delay
        self.fail_account = fail_account
        self.calls: list[tuple[str, str]] = []
        self.instances: list[PoolDirect] = []
        self.lock = threading.Lock()

    def __call__(self) -> PoolDirect:
        with self.lock:
            coordinator = not self.instances
            direct = PoolDirect(
                self.directory,
                self.calls,
                self.lock,
                coordinator=coordinator,
                delay=self.delay,
                fail_account=self.fail_account,
            )
            self.instances.append(direct)
        return direct


def pool_client(factory: PoolDirectFactory) -> TestClient:
    return TestClient(create_app(
        runner=FakeRunner(),
        direct_factory=factory,  # type: ignore[arg-type]
        license_manager=AllowAllLicense(),  # type: ignore[arg-type]
    ))


def test_direct_pool_runs_real_endpoint_in_parallel_and_preserves_order() -> None:
    directory = [
        {"name": f"head{index}", "id": f"account-{index}"}
        for index in range(1, 6)
    ]
    delay = 0.04
    serial_factory = PoolDirectFactory(directory, delay=delay)
    serial_started = time.perf_counter()
    coordinator = serial_factory()
    serial_directory = coordinator.account_directory()
    coordinator.close()
    for account in serial_directory:
        worker = serial_factory()
        try:
            selected = worker.select_known_account(account)
            worker.credits_for_verified_account(selected)
            worker.generating_count()
        finally:
            worker.close()
    serial_elapsed = time.perf_counter() - serial_started

    factory = PoolDirectFactory(directory, delay=delay)
    started = time.perf_counter()
    response = pool_client(factory).get("/v1/direct/pool")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert [row["name"] for row in response.json()["data"]] == [
        account["name"] for account in directory
    ]
    assert elapsed < serial_elapsed / 2
    assert len(factory.instances) == len(directory) + 1
    assert len({id(instance) for instance in factory.instances}) == len(factory.instances)
    for account in directory:
        name = account["name"]
        assert ("select", name) in factory.calls
        assert ("credits", name) in factory.calls
        assert ("generating", name) in factory.calls
    assert all(instance.closed for instance in factory.instances)


def test_direct_pool_whitelist_is_pushed_down_and_supports_unicode() -> None:
    directory = [
        {"name": "main-a", "id": "account-1"},
        {"name": "legacy-a", "id": "account-5"},
        {"name": "main-c", "id": "account-3"},
    ]
    factory = PoolDirectFactory(directory)
    response = pool_client(factory).get(
        "/v1/direct/pool",
        params=[("accounts", "legacy-a"), ("accounts", "main-a")],
    )
    assert response.status_code == 200
    assert [row["name"] for row in response.json()["data"]] == ["main-a", "legacy-a"]
    queried = {account for operation, account in factory.calls if operation == "credits"}
    assert queried == {"main-a", "legacy-a"}
    assert "main-c" not in {account for _operation, account in factory.calls}
    assert len(factory.instances) == 3


def test_direct_pool_rejects_unknown_account_without_spawning_workers() -> None:
    factory = PoolDirectFactory([{"name": "main-a", "id": "account-1"}])
    response = pool_client(factory).get("/v1/direct/pool?accounts=missing")
    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "unknown_account",
        "message": "One or more Symphony accounts do not exist",
        "details": {"accounts": ["missing"]},
    }
    assert factory.calls == [("directory", "")]
    assert len(factory.instances) == 1
    assert factory.instances[0].closed is True


def test_direct_pool_isolates_one_account_failure_and_keeps_order() -> None:
    directory = [
        {"name": "main-a", "id": "account-1"},
        {"name": "main-b", "id": "account-2"},
        {"name": "main-c", "id": "account-3"},
    ]
    response = pool_client(PoolDirectFactory(directory, fail_account="main-b")).get(
        "/v1/direct/pool"
    )
    assert response.status_code == 200
    rows = response.json()["data"]
    assert [row["name"] for row in rows] == ["main-a", "main-b", "main-c"]
    assert rows[1] == {"name": "main-b", "error": "account temporarily unavailable"}


@pytest.mark.parametrize(("url", "tail"), [
    ("/v1/credits", ["credits", "--window", "background", "--site-session", "persistent", "-f", "json"]),
    ("/v1/jobs?limit=7&pending=true", ["jobs", "--limit", "7", "--pending", "true", "-f", "json"]),
    ("/v1/jobs?limit=3&pending=false", ["jobs", "--limit", "3", "--pending", "false", "-f", "json"]),
    ("/v1/generations?limit=8", ["generations", "--limit", "8", "-f", "json"]),
])
def test_get_endpoints_build_safe_argv(url: str, tail: list[str]) -> None:
    runner = FakeRunner([CLIResult(0, '{"items":[1]}', "")]); response = client_for(runner).get(url)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": {"items": [1]}}
    assert runner.calls == [(["node-test", "./cli/main.js", "tiktok-symphony", *tail], 30)]


def test_browser_endpoint_routes_to_explicit_opencli_profile() -> None:
    runner = FakeRunner([CLIResult(0, '{"credits":2000}', "")])
    response = client_for(runner).get("/v1/credits?main_account=main-b")
    assert response.status_code == 200
    assert runner.calls == [([
        "node-test", "./cli/main.js", "--profile", "main-b",
        "tiktok-symphony", "credits", "--window", "background",
        "--site-session", "persistent", "-f", "json",
    ], 30)]

def test_generate_video_builds_argv_and_checks_refs(tmp_path: Path) -> None:
    first = tmp_path / "first.png"; last = tmp_path / "last.png"
    first.write_bytes(b"fake"); last.write_bytes(b"fake")
    runner = FakeRunner([CLIResult(0, '{"id":"gen-1"}', "")])
    payload = {"prompt": "safe; $(argument)", "refs": [str(first), str(last)], "mode": "reference", "frames": "first-last", "model": "Dreamina Seedance 2.0", "duration": 15, "wait": True, "timeout": 77}
    response = client_for(runner).post("/v1/videos/generations", json=payload)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": {"id": "gen-1"}}
    assert runner.calls == [(["node-test", "./cli/main.js", "tiktok-symphony", "generate-video", payload["prompt"], "--model", "Dreamina Seedance 2.0", "--refs", f"{first},{last}", "--mode", "reference", "--frames", "first-last", "--duration", "15", "--wait", "true", "--timeout", "77", "--window", "background", "--site-session", "persistent", "-f", "json"], 77)]


def test_direct_generation_hard_stops_sixth_unobserved_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"fake")
    submitted: list[str] = []

    class FakeDirect:
        def select_account(self, account: str) -> dict[str, str]:
            assert account == "main-a"
            return {"name": "main-a", "id": "account-1"}

        def generating_count(self) -> int:
            # Simulate the platform counter lagging behind fresh submissions.
            return 0

        def check_task(self, _task_id: str) -> dict[str, Any]:
            # Still rendering: no error, no content yet.
            return {"hasContent": False}

        def generate_reference_video(self, **_kwargs: Any) -> dict[str, Any]:
            task_id = f"task-{len(submitted) + 1}"
            submitted.append(task_id)
            return {"data": {"task_id": task_id}}

        def close(self) -> None:
            return None

    monkeypatch.setattr(main_module.AssetCache, "resolve", lambda _self, _refs: [])
    client = TestClient(create_app(
        runner=FakeRunner(),
        direct_factory=FakeDirect,  # type: ignore[arg-type]
        license_manager=AllowAllLicense(),  # type: ignore[arg-type]
    ))
    payload = {
        "prompt": "capacity test",
        "refs": [str(ref)],
        "mode": "reference",
        "model": "Dreamina Seedance 2.0",
        "duration": 5,
        "wait": False,
        "backend": "direct",
        "account": "main-a",
    }

    responses = [client.post("/v1/videos/generations", json=payload) for _ in range(6)]

    assert [response.status_code for response in responses] == [200, 200, 200, 200, 200, 429]
    assert submitted == ["task-1", "task-2", "task-3", "task-4", "task-5"]
    assert responses[-1].json()["error"] == {
        "code": "account_capacity_exhausted",
        "message": "This Symphony sub-account already has 5 verified active generations",
        "details": {
            "account": "main-a",
            "generating": 5,
            "reserved": 0,
            "platformCount": 0,
            "tracked": 5,
            "external": 0,
            "effective": 5,
            "limit": 5,
        },
    }


def test_zombie_platform_count_is_verified_and_released(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """根治回归（V35）：平台计数虚报满额、但登记任务逐条核实已终结时，
    闸门必须放行，不得在僵尸计数上拒绝提交（2026-08-04 事故：账本仅能
    解释约 17 条时平台报 75，全池假满空等）。"""
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"fake")
    submitted: list[str] = []

    class FakeDirect:
        remote = 0  # 平台侧计数（类属性，模拟计数滞后与虚报）
        tasks_terminal = False

        def select_account(self, _account: str) -> dict[str, str]:
            return {"name": "main-a", "id": "account-1"}

        def generating_count(self) -> int:
            return FakeDirect.remote

        def check_task(self, _task_id: str) -> dict[str, Any]:
            if FakeDirect.tasks_terminal:
                return {"failReason": "render failed", "hasContent": False}
            return {"hasContent": False}

        def generate_reference_video(self, **_kwargs: Any) -> dict[str, Any]:
            task_id = f"task-{len(submitted) + 1}"
            submitted.append(task_id)
            return {"data": {"task_id": task_id}}

        def close(self) -> None:
            return None

    monkeypatch.setattr(main_module.AssetCache, "resolve", lambda _self, _refs: [])
    client = TestClient(create_app(
        runner=FakeRunner(),
        direct_factory=FakeDirect,  # type: ignore[arg-type]
        license_manager=AllowAllLicense(),  # type: ignore[arg-type]
    ))
    payload = {
        "prompt": "zombie test",
        "refs": [str(ref)],
        "mode": "reference",
        "model": "Dreamina Seedance 2.0",
        "duration": 5,
        "wait": False,
        "backend": "direct",
        "account": "main-a",
    }

    # 填满 5 个登记槽位
    for _ in range(5):
        assert client.post("/v1/videos/generations", json=payload).status_code == 200

    # 平台计数报 5（其实这 5 条已全部失败终结）——旧实现会在这里 429 空等
    FakeDirect.remote = 5
    FakeDirect.tasks_terminal = True
    response = client.post("/v1/videos/generations", json=payload)
    assert response.status_code == 200
    assert len(submitted) == 6

    # 池视图同口径：真实占用 1（第 6 条），不再转述平台的虚报计数
    count = client.get("/v1/direct/generating-count?account=main-a").json()["data"]
    assert count["generating"] == count["effective"]
    assert count["effective"] <= 5
    assert count["platformCount"] == 5


def test_direct_generation_rejects_when_platform_already_reports_five(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"fake")

    class FakeDirect:
        called = False

        def select_account(self, _account: str) -> dict[str, str]:
            return {"name": "main-b", "id": "account-2"}

        def generating_count(self) -> int:
            return 5

        def generate_reference_video(self, **_kwargs: Any) -> dict[str, Any]:
            self.called = True
            raise AssertionError("submission must not be called at capacity")

        def close(self) -> None:
            return None

    monkeypatch.setattr(main_module.AssetCache, "resolve", lambda _self, _refs: [])
    direct = FakeDirect()
    client = TestClient(create_app(
        runner=FakeRunner(),
        direct_factory=lambda: direct,  # type: ignore[arg-type]
        license_manager=AllowAllLicense(),  # type: ignore[arg-type]
    ))

    response = client.post("/v1/videos/generations", json={
        "prompt": "capacity test",
        "refs": [str(ref)],
        "mode": "reference",
        "model": "Dreamina Seedance 2.0",
        "duration": 5,
        "wait": False,
        "backend": "direct",
        "account": "main-b",
    })

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "account_capacity_exhausted"
    assert direct.called is False


def test_pool_and_count_report_gate_reserved_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """池/在渲数接口必须计入闸门登记的在渲任务，否则批量调度器视图偏乐观白吃 429。"""
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"fake")
    submitted: list[str] = []

    class FakeDirect:
        remote = 0  # 平台侧 generating 计数，类属性模拟"平台稍后才跟上"

        def select_account(self, _account: str) -> dict[str, str]:
            return {"name": "main-a", "id": "account-1"}

        def current_account(self) -> dict[str, str]:
            return {"name": "main-a", "id": "account-1"}

        def account_directory(self) -> list[dict[str, str]]:
            return [{"name": "main-a", "id": "account-1"}]

        def select_known_account(self, account: dict[str, str]) -> dict[str, str]:
            return dict(account)

        def credits_for_verified_account(self, _selected: dict[str, str]) -> dict[str, Any]:
            return {"credits": 100}

        def generating_count(self) -> int:
            return FakeDirect.remote

        def check_task(self, _task_id: str) -> dict[str, Any]:
            return {"hasContent": False}

        def generate_reference_video(self, **_kwargs: Any) -> dict[str, Any]:
            task_id = f"task-{len(submitted) + 1}"
            submitted.append(task_id)
            return {"data": {"task_id": task_id}}

        def close(self) -> None:
            return None

    monkeypatch.setattr(main_module.AssetCache, "resolve", lambda _self, _refs: [])
    client = TestClient(create_app(
        runner=FakeRunner(),
        direct_factory=FakeDirect,  # type: ignore[arg-type]
        license_manager=AllowAllLicense(),  # type: ignore[arg-type]
    ))
    payload = {
        "prompt": "reserved test",
        "refs": [str(ref)],
        "mode": "reference",
        "model": "Dreamina Seedance 2.0",
        "duration": 5,
        "wait": False,
        "backend": "direct",
        "account": "main-a",
    }
    for _ in range(2):
        assert client.post("/v1/videos/generations", json=payload).status_code == 200

    row = client.get("/v1/direct/pool").json()["data"][0]
    assert row == {
        "name": "main-a", "credits": 100,
        "generating": 2, "reserved": 0, "platformCount": 0,
        "tracked": 2, "external": 0, "effective": 2, "limit": 5,
    }
    count = client.get("/v1/direct/generating-count?account=main-a").json()["data"]
    assert count == {
        "account": "main-a",
        "generating": 2, "reserved": 0, "platformCount": 0,
        "tracked": 2, "external": 0, "effective": 2, "limit": 5,
    }

    # 平台计数跟上后，登记任务与平台计数不得重复计数
    FakeDirect.remote = 2
    row = client.get("/v1/direct/pool").json()["data"][0]
    assert (row["generating"], row["reserved"], row["effective"]) == (2, 0, 2)

def test_missing_ref_returns_422_without_invocation(tmp_path: Path) -> None:
    runner = FakeRunner(); response = client_for(runner).post("/v1/videos/generations", json={"prompt": "x", "refs": [str(tmp_path / "missing.png")], "mode": "image"})
    assert response.status_code == 422
    assert response.json()["ok"] is False and response.json()["error"]["code"] == "validation_error"
    assert runner.calls == []

def test_generate_accepts_nine_references(tmp_path: Path) -> None:
    refs = [tmp_path / f"{i}.jpg" for i in range(9)]
    for ref in refs:
        ref.write_bytes(b"fake")
    runner = FakeRunner([CLIResult(0, '{"id":"gen-9"}', "")])
    response = client_for(runner).post("/v1/videos/generations", json={
        "prompt": "nine references",
        "refs": [str(ref) for ref in refs],
        "mode": "reference",
    })
    assert response.status_code == 200
    assert "--refs" in runner.calls[0][0]

def test_generate_rejects_more_than_nine_references(tmp_path: Path) -> None:
    refs = [tmp_path / f"{i}.jpg" for i in range(10)]
    for ref in refs:
        ref.write_bytes(b"fake")
    runner = FakeRunner()
    response = client_for(runner).post("/v1/videos/generations", json={
        "prompt": "too many references",
        "refs": [str(ref) for ref in refs],
        "mode": "reference",
    })
    assert response.status_code == 422
    assert runner.calls == []

@pytest.mark.parametrize("field,value", [("mode", "bad"), ("frames", "middle"), ("duration", 6), ("timeout", 0)])
def test_generate_schema_validation_is_uniform_422(field: str, value: Any) -> None:
    response = client_for(FakeRunner()).post("/v1/videos/generations", json={"prompt": "x", field: value})
    assert response.status_code == 422
    assert response.json()["ok"] is False and response.json()["error"]["code"] == "validation_error"

def test_explicit_account_never_silently_uses_browser_backend(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"fake")
    runner = FakeRunner()
    response = client_for(runner).post("/v1/videos/generations", json={
        "prompt": "x",
        "refs": [str(ref)],
        "mode": "reference",
        "account": "main-a",
        "backend": "browser",
    })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "direct_account_required"
    assert runner.calls == []

def test_download_builds_argv_with_optional_out(tmp_path: Path) -> None:
    out = tmp_path / "clip.mp4"; runner = FakeRunner([CLIResult(0, '{"path":"clip.mp4"}', "")])
    response = client_for(runner).post("/v1/assets/asset-123/download", json={
        "out": str(out),
        "allow_preview": True,
        "timeout": 90,
    })
    assert response.status_code == 200
    assert runner.calls == [(["node-test", "./cli/main.js", "tiktok-symphony", "download", "asset-123", "--allow-preview", "true", "--timeout", "90", "--out", str(out), "-f", "json"], 105)]

def test_cli_empty_result_maps_to_empty_list_only_for_list_endpoints() -> None:
    empty = CLIResult(66, "", "code: EMPTY_RESULT")
    jobs = client_for(FakeRunner([empty])).get("/v1/jobs")
    generations = client_for(FakeRunner([empty])).get("/v1/generations")

    assert jobs.status_code == 200
    assert jobs.json() == {"ok": True, "data": []}
    assert generations.status_code == 200
    assert generations.json() == {"ok": True, "data": []}


def test_cli_failure_maps_to_502() -> None:
    response = client_for(FakeRunner([CLIResult(9, "", "authentication failed")])).get("/v1/credits")
    assert response.status_code == 502
    assert response.json() == {"ok": False, "error": {"code": "cli_error", "message": "OpenCLI command failed", "details": {"exit_code": 9, "stderr": "authentication failed"}}}

def test_timeout_maps_to_502() -> None:
    response = client_for(FakeRunner([CLITimeout(5)])).get("/v1/credits")
    assert response.status_code == 502
    assert response.json()["error"] == {"code": "cli_timeout", "message": "OpenCLI command timed out", "details": {"timeout": 5}}

def test_non_json_cli_output_is_wrapped() -> None:
    response = client_for(FakeRunner([CLIResult(0, "plain output", "")])).get("/v1/credits")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": {"output": "plain output"}}
