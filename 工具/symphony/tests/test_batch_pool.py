from __future__ import annotations

import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
SPEC = importlib.util.spec_from_file_location(
    "symphony_video_batch_under_test",
    PROJECT_ROOT / "symphony_video_batch.py",
)
assert SPEC is not None and SPEC.loader is not None
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)


def test_account_pool_pushes_allowed_accounts_with_url_encoding(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_api_json(url: str, *, timeout: int, **_kwargs):
        seen["url"] = url
        seen["timeout"] = timeout
        return {
            "data": [
                {"name": "main-a", "credits": 1000, "generating": 0, "limit": 5},
                {"name": "main-a", "credits": 900, "generating": 1, "limit": 5},
            ]
        }

    monkeypatch.setattr(batch, "api_json", fake_api_json)
    rows = batch.account_pool(
        "http://127.0.0.1:8765",
        ["main-a", "main-a"],
    )

    parsed = urllib.parse.urlsplit(str(seen["url"]))
    assert parsed.path == "/v1/direct/pool"
    assert urllib.parse.parse_qs(parsed.query) == {
        "accounts": ["main-a", "main-a"],
    }
    assert seen["timeout"] == 180
    assert [row["name"] for row in rows] == ["main-a", "main-a"]


def test_account_pool_without_whitelist_keeps_plain_endpoint(monkeypatch) -> None:
    seen: list[str] = []

    def fake_api_json(url: str, **_kwargs):
        seen.append(url)
        return {"data": []}

    monkeypatch.setattr(batch, "api_json", fake_api_json)
    assert batch.account_pool("http://127.0.0.1:8765", None) == []
    assert seen == ["http://127.0.0.1:8765/v1/direct/pool"]


def test_account_pool_routes_to_named_main_account(monkeypatch) -> None:
    seen: list[str] = []

    def fake_api_json(url: str, **_kwargs):
        seen.append(url)
        return {"data": [{"name": "4", "credits": 2000, "generating": 0, "limit": 5}]}

    monkeypatch.setattr(batch, "api_json", fake_api_json)
    rows = batch.account_pool("http://127.0.0.1:8765", None, "main-b")
    parsed = urllib.parse.urlsplit(seen[0])
    assert urllib.parse.parse_qs(parsed.query) == {"main_account": ["main-b"]}
    assert rows[0]["name"] == "4"


def test_pool_scheduler_keeps_daily_limited_account_disabled_after_refresh(monkeypatch) -> None:
    rows = [
        {"name": "main-a", "credits": 1000, "generating": 0, "limit": 5},
        {"name": "main-b", "credits": 900, "generating": 0, "limit": 5},
    ]
    monkeypatch.setattr(
        batch,
        "account_pool",
        lambda _base, _allowed, _main=None, **_kwargs: [dict(row) for row in rows],
    )
    monkeypatch.setattr(batch, "load_account_limits", dict)

    scheduler = batch.PoolScheduler("http://127.0.0.1:8765", None, 5)
    scheduler.disable((None, "main-a"))
    scheduler.refresh()

    assert set(scheduler.rows) == {(None, "main-b")}
    assert scheduler.disabled_accounts == {(None, "main-a")}
    assert scheduler.all_accounts_disabled() is False

    scheduler.disable((None, "main-b"))
    scheduler.refresh()
    assert scheduler.rows == {}
    assert scheduler.all_accounts_disabled() is True


def test_multi_account_pool_tags_rows_with_their_main_account(monkeypatch) -> None:
    by_main = {
        "main-a": [{"name": "main-a", "credits": 100, "generating": 0, "limit": 5}],
        "main-b": [{"name": "1", "credits": 200, "generating": 1, "limit": 5}],
    }
    monkeypatch.setattr(
        batch,
        "account_pool",
        lambda _base, _allowed, main=None, **_kwargs: [dict(r) for r in by_main[main]],
    )

    rows = batch.multi_account_pool("http://x", None, ["main-a", "main-b"])

    assert [batch.pool_key(row) for row in rows] == [
        ("main-a", "main-a"),
        ("main-b", "1"),
    ]
    assert [batch.account_label(row) for row in rows] == ["main-a/main-a", "main-b/1"]


def test_multi_account_pool_rejects_unknown_name_across_all_mains(monkeypatch) -> None:
    monkeypatch.setattr(
        batch,
        "account_pool",
        lambda _base, allowed, main=None, **_kwargs: (
            [{"name": "main-a", "credits": 1, "generating": 0, "limit": 5}]
            if main == "main-a" and "main-a" in (allowed or [])
            else []
        ),
    )

    # main-a 只在 main-a 下：单个主账号查不到不该报错，并集里查不到才报错
    rows = batch.multi_account_pool("http://x", ["main-a"], ["main-a", "main-b"])
    assert [row["name"] for row in rows] == ["main-a"]

    try:
        batch.multi_account_pool("http://x", ["main-a", "nope"], ["main-a", "main-b"])
    except RuntimeError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("未知子账号名应当报错")


def test_pool_scheduler_isolates_same_named_subaccounts_of_different_mains(monkeypatch) -> None:
    """两个主账号下都有子账号 "1" 时，必须是两条独立的账号，不能互相覆盖。"""
    by_main = {
        "A": [{"name": "1", "credits": 100, "generating": 0, "limit": 5}],
        "B": [{"name": "1", "credits": 200, "generating": 4, "limit": 5}],
    }
    monkeypatch.setattr(
        batch,
        "account_pool",
        lambda _base, _allowed, main=None, **_kwargs: [dict(r) for r in by_main[main]],
    )
    monkeypatch.setattr(batch, "load_account_limits", dict)

    scheduler = batch.PoolScheduler("http://x", None, 5, ["A", "B"])
    assert set(scheduler.rows) == {("A", "1"), ("B", "1")}

    # 在渲少的优先：A/1 (0/5) 排在 B/1 (4/5) 前面
    assert batch.pool_key(scheduler.pick(15)) == ("A", "1")

    scheduler.disable(("A", "1"))
    assert batch.pool_key(scheduler.pick(15)) == ("B", "1")
    assert scheduler.all_accounts_disabled() is False


def test_account_limit_mark_is_scoped_to_main_account() -> None:
    marks = {"A/1": {"limited_at": "x"}, "head9": {"limited_at": "legacy"}}
    assert batch.account_limit_mark(marks, "A", "1") is not None
    assert batch.account_limit_mark(marks, "B", "1") is None  # 同名不同主账号不牵连
    # 2026-08-01 之前写的裸名标记仍然认
    assert batch.account_limit_mark(marks, "A", "head9") is not None


def test_mark_account_limited_round_trip_is_main_scoped(monkeypatch, tmp_path) -> None:
    """写盘 → 读回 → 命中，是 submit 自动跳过限额账号的唯一依据（V29）。

    2026-08-01 的教训：main-a 的 5 个子账号全部触发日限，但标记文件根本没生成，
    后续批次照样往上投。这个链路必须有测试。
    """
    marks_file = tmp_path / "账号限制.json"
    monkeypatch.setattr(batch, "LIMIT_MARKS_PATH", marks_file)

    batch.mark_account_limited("main-a", "User Generation Day Limit", "main-a")
    assert marks_file.is_file(), "触发日限必须落盘，否则下一批还会往这个账号投"

    marks = batch.load_account_limits()
    assert set(marks) == {"main-a/main-a"}
    assert batch.account_limit_mark(marks, "main-a", "main-a") is not None
    # 另一个主账号下的同名子账号不受牵连
    assert batch.account_limit_mark(marks, "main-b", "main-a") is None


def test_load_account_limits_drops_marks_from_a_previous_quota_day(monkeypatch, tmp_path) -> None:
    """额度每天早 8 点刷新，昨天的标记读时即弃，不需要手工清理。"""
    marks_file = tmp_path / "账号限制.json"
    monkeypatch.setattr(batch, "LIMIT_MARKS_PATH", marks_file)

    stale = batch.quota_window_start() - 3600  # 本额度日起点之前 = 上一个额度日
    fresh = batch.quota_window_start() + 60
    marks_file.write_text(
        json.dumps({
            "main-a/main-a": {"limited_at_epoch": stale},
            "main-a/main-b": {"limited_at_epoch": fresh},
        }),
        encoding="utf-8",
    )

    assert set(batch.load_account_limits()) == {"main-a/main-b"}


def test_pool_scheduler_skips_accounts_marked_limited_on_disk(monkeypatch) -> None:
    """池调度器启动时就要把已标记的账号排除，而不是等它们各挨一次平台拒绝。"""
    monkeypatch.setattr(
        batch,
        "account_pool",
        lambda _base, _allowed, main=None, **_kwargs: [
            {"name": "main-a", "credits": 900, "generating": 0, "limit": 5},
            {"name": "main-b", "credits": 900, "generating": 0, "limit": 5},
        ],
    )
    monkeypatch.setattr(
        batch, "load_account_limits",
        lambda: {"main-a/main-a": {"limited_at": "2026-08-01T09:26:23+0800"}},
    )

    scheduler = batch.PoolScheduler("http://x", None, 5, ["main-a"])

    assert set(scheduler.rows) == {("main-a", "main-b")}
    assert batch.pool_key(scheduler.pick(15)) == ("main-a", "main-b")


def test_resolve_main_accounts() -> None:
    status = {"main_accounts": [{"main_account": "main-a"}, {"main_account": "main-b"}]}

    assert batch.resolve_main_accounts(status, None, "main-a") == ["main-a"]
    assert batch.resolve_main_accounts(status, None, None) == []
    assert batch.resolve_main_accounts(status, "all", None) == ["main-a", "main-b"]
    assert batch.resolve_main_accounts(status, "main-b", "main-a") == ["main-b"]

    try:
        batch.resolve_main_accounts(status, "nosuch", None)
    except RuntimeError as exc:
        assert "nosuch" in str(exc)
    else:
        raise AssertionError("未登录的主账号应当报错")


def test_account_pool_folds_proxy_reserved_into_generating(monkeypatch) -> None:
    """代理提交闸门的短时保留同样占槽位，调度视图必须折进 generating 统一记账。"""
    monkeypatch.setattr(batch, "api_json", lambda url, **_kwargs: {
        "data": [
            {"name": "main-a", "credits": 100, "generating": 1, "reserved": 2, "effective": 3, "limit": 5},
            {"name": "main-b", "credits": 100, "generating": 4, "limit": 5},
            {"name": "main-c", "error": "boom"},
        ]
    })

    rows = batch.account_pool("http://127.0.0.1:8765", None)

    assert rows[0]["generating"] == 3
    assert rows[1]["generating"] == 4  # 旧版代理没有 reserved 字段时保持原值
    assert rows[2] == {"name": "main-c", "error": "boom"}  # error 行不动


def test_generating_count_includes_proxy_reserved(monkeypatch) -> None:
    monkeypatch.setattr(batch, "api_json", lambda url, **_kwargs: {
        "data": {"account": "main-a", "generating": 2, "reserved": 3, "effective": 5, "limit": 5}
    })
    assert batch.generating_count("http://127.0.0.1:8765", "main-a") == (5, 5)


def _watch_argv(ledger_path: str) -> list[str]:
    return [
        "symphony_video_batch.py", "submit",
        "--story-root", "stories/99-测试", "--units", "1",
        "--model", "Dreamina Seedance 2.0", "--confirm-credits", "15",
        "--ledger", ledger_path, "--watch",
    ]


def test_watch_runs_poll_after_submit(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    ledger = tmp_path / "ledger.jsonl"

    def fake_submit(_args):
        ledger.write_text('{"unit":1,"status":"submitted","task_id":"t"}\n', encoding="utf-8")
        calls.append("submit")
        return 0

    monkeypatch.setattr(batch, "submit", fake_submit)
    monkeypatch.setattr(batch, "poll", lambda _args: calls.append("poll") or 0)
    monkeypatch.setattr(batch.sys, "argv", _watch_argv(str(ledger)))

    assert batch.main() == 0
    assert calls == ["submit", "poll"]


def test_watch_propagates_submit_errors_through_poll_exit(monkeypatch, tmp_path) -> None:
    """部分单元提交失败（rc=1）仍要轮询已提交的，但整体退出码不得被 poll 的 0 洗白。"""
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text('{"unit":1,"status":"submitted","task_id":"t"}\n', encoding="utf-8")
    monkeypatch.setattr(batch, "submit", lambda _args: 1)
    monkeypatch.setattr(batch, "poll", lambda _args: 0)
    monkeypatch.setattr(batch.sys, "argv", _watch_argv(str(ledger)))

    assert batch.main() == 1


def test_watch_skips_poll_on_unknown_outcome(monkeypatch, tmp_path) -> None:
    """unknown_outcome（可能已扣费的悬案）必须停在人工核实，不得自动进入轮询。"""
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text('{"unit":1,"status":"unknown_outcome"}\n', encoding="utf-8")

    def forbidden_poll(_args):
        raise AssertionError("unknown_outcome 停批后不得自动轮询")

    monkeypatch.setattr(batch, "submit", lambda _args: 2)
    monkeypatch.setattr(batch, "poll", forbidden_poll)
    monkeypatch.setattr(batch.sys, "argv", _watch_argv(str(ledger)))

    assert batch.main() == 2


def test_watch_skips_poll_when_ledger_never_created(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(batch, "submit", lambda _args: 1)
    monkeypatch.setattr(
        batch, "poll",
        lambda _args: (_ for _ in ()).throw(AssertionError("空账本不该进入轮询")),
    )
    monkeypatch.setattr(batch.sys, "argv", _watch_argv(str(tmp_path / "没有的账本.jsonl")))

    assert batch.main() == 1
