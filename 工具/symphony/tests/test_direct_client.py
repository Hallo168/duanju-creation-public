from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from app.direct_client import (
    AssetCache,
    CachedAsset,
    DirectOutcomeUnknown,
    DirectRejected,
    DirectUnavailable,
    SymphonyDirectClient,
)
from app.session_store import list_main_accounts, load_session, migrate_legacy_session, save_session


SESSION = {
    "version": 1,
    "cookies": [{"name": "sid", "value": "secret"}],
    "csrf": "csrf-secret",
    "fp_id": "fp-secret",
    "device_id": "device-secret",
}


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.values[(service, account)] = password


def test_session_round_trip_uses_password_store() -> None:
    store = MemoryStore()
    save_session(SESSION, store)
    assert load_session(store) == SESSION
    assert any("secret" in value for value in store.values.values())


def test_large_session_is_split_into_small_credentials() -> None:
    store = MemoryStore()
    large = {**SESSION, "cookies": [{"name": "c", "value": "x" * 6000}]}
    save_session(large, store)
    assert load_session(store) == large
    assert max(map(len, store.values.values())) <= 800


def test_named_main_account_sessions_are_isolated_and_indexed() -> None:
    store = MemoryStore()
    first = {**SESSION, "browser_profile": "main-a", "accounts": [
        {"name": "main-a", "id": "account-1"},
    ]}
    second = {**SESSION, "browser_profile": "main-b", "cookies": [
        {"name": "sid", "value": "second-secret"},
    ], "accounts": [
        {"name": "new1", "id": "account-2"},
        {"name": "new2", "id": "account-3"},
    ]}
    save_session(first, store, main_account="main-a")
    save_session(second, store, main_account="main-b")
    assert load_session(store, main_account="main-a") == first
    assert load_session(store, main_account="main-b") == second
    assert [item["account_count"] for item in list_main_accounts(store)] == [1, 2]


def test_legacy_session_can_be_copied_without_removing_original() -> None:
    store = MemoryStore()
    save_session(SESSION, store)
    migrate_legacy_session("main-a", browser_profile="main-a", store=store)
    assert load_session(store) == SESSION
    named = load_session(store, main_account="main-a")
    assert named is not None
    assert named["browser_profile"] == "main-a"
    assert named["main_account"] == "main-a"


def test_asset_cache_resolves_by_content_hash(tmp_path: Path) -> None:
    image = tmp_path / "hero.png"
    image.write_bytes(b"image-bytes")
    digest = hashlib.sha256(b"image-bytes").hexdigest()
    cache = tmp_path / "assets.json"
    cache.write_text(json.dumps({digest: {"url": "https://cdn.example/hero", "name": "hero.png"}}), encoding="utf-8")
    assert AssetCache(cache).resolve([str(image)]) == [
        CachedAsset(name="hero.png", url="https://cdn.example/hero")
    ]


def test_direct_count_sends_session_without_exposing_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-csrftoken"] == "csrf-secret"
        assert request.headers["cookie"] == "sid=secret"
        assert "device-secret" in str(request.url)
        return httpx.Response(200, json={"code": 0, "data": {"total": 3}})

    client = SymphonyDirectClient(session=SESSION, transport=httpx.MockTransport(handler))
    try:
        assert client.generating_count() == 3
    finally:
        client.close()

def test_direct_account_switch_changes_only_selector_cookie_and_verifies() -> None:
    session = {
        **SESSION,
        "cookies": [
            {"name": "sid", "value": "secret"},
            {
                "name": "s_aio_client_id",
                "value": "account-1",
                "domain": ".tiktok.com",
                "path": "/",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("ClientGetAccountList"):
            return httpx.Response(200, json={
                "BaseResp": {"StatusCode": 0},
                "accounts": [
                    {"profileName": "main-a", "aioClientID": "account-1"},
                    {"profileName": "main-b", "aioClientID": "account-2"},
                ],
            })
        if path.endswith("ClientGetAccountInfo"):
            active = "account-2" if "s_aio_client_id=account-2" in request.headers["cookie"] else "account-1"
            return httpx.Response(200, json={
                "BaseResp": {"StatusCode": 0},
                "account": {
                    "profileName": "main-b" if active == "account-2" else "main-a",
                    "aioClientID": active,
                },
            })
        raise AssertionError(path)

    client = SymphonyDirectClient(session=session, transport=httpx.MockTransport(handler))
    try:
        assert client.select_account("main-b") == {"name": "main-b", "id": "account-2"}
        assert client.current_account()["name"] == "main-b"
        assert next(
            cookie["value"] for cookie in session["cookies"]
            if cookie["name"] == "s_aio_client_id"
        ) == "account-2"
    finally:
        client.close()


def test_known_account_selection_keeps_parallel_cookie_jars_isolated() -> None:
    session = {
        **SESSION,
        "cookies": [
            {"name": "sid", "value": "secret"},
            {
                "name": "s_aio_client_id",
                "value": "account-1",
                "domain": ".tiktok.com",
                "path": "/",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("ClientGetAccountInfo")
        cookie = request.headers["cookie"]
        active = "account-2" if "s_aio_client_id=account-2" in cookie else "account-1"
        return httpx.Response(200, json={
            "BaseResp": {"StatusCode": 0},
            "account": {
                "profileName": "main-b" if active == "account-2" else "main-a",
                "aioClientID": active,
            },
        })

    transport = httpx.MockTransport(handler)
    worker_sessions = [copy.deepcopy(session), copy.deepcopy(session)]
    clients = [
        SymphonyDirectClient(session=value, transport=transport)
        for value in worker_sessions
    ]
    assert worker_sessions[0] is not worker_sessions[1]
    assert clients[0] is not clients[1]
    assert clients[0].client is not clients[1].client
    accounts = [
        {"name": "main-a", "id": "account-1"},
        {"name": "main-b", "id": "account-2"},
    ]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            selected = list(executor.map(
                lambda pair: pair[0].select_known_account(pair[1]),
                zip(clients, accounts),
            ))
        assert selected == accounts
        assert [
            next(
                cookie["value"]
                for cookie in value["cookies"]
                if cookie["name"] == "s_aio_client_id"
            )
            for value in worker_sessions
        ] == ["account-1", "account-2"]
        assert next(
            cookie["value"]
            for cookie in session["cookies"]
            if cookie["name"] == "s_aio_client_id"
        ) == "account-1"
    finally:
        for client in clients:
            client.close()


def test_direct_generation_builds_captured_r2v_shape() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "data": {"task_id": "task-1"}})

    client = SymphonyDirectClient(session=SESSION, transport=httpx.MockTransport(handler))
    try:
        result = client.generate_reference_video(
            prompt="three shots",
            assets=[CachedAsset("hero.png", "https://cdn.example/hero")],
            duration=15,
            model="Dreamina Seedance 2.0",
        )
    finally:
        client.close()
    assert result["data"]["task_id"] == "task-1"
    assert seen["model"] == "2000004"
    assert seen["images"] == ["https://cdn.example/hero"]
    assert json.loads(seen["settings"])["images"][0]["label"] == "image 1"
    assert seen["mentions"] == [{"type": 1, "id": "https://cdn.example/hero"}]


def test_direct_task_check_selects_matching_draft() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {"draft_infos": [
            {"taskId": "other", "renderTaskStatus": 2},
            {"taskId": "task-1", "renderTaskStatus": 3, "vid": "asset-1"},
        ]}})

    client = SymphonyDirectClient(session=SESSION, transport=httpx.MockTransport(handler))
    try:
        assert client.check_task("task-1")["vid"] == "asset-1"
    finally:
        client.close()


def test_original_video_url_requires_matching_asset_id() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {
            "downloadable_url": "https://cdn.example/video.mp4?vid=asset-1",
        }})

    client = SymphonyDirectClient(session=SESSION, transport=httpx.MockTransport(handler))
    try:
        assert client.original_video_url("asset-1").endswith("vid=asset-1")
        with pytest.raises(DirectUnavailable):
            client.original_video_url("asset-2")
    finally:
        client.close()

def test_direct_download_sends_referer_and_honors_exact_output_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = SymphonyDirectClient(session=SESSION, transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"code": 0, "data": {
            "downloadable_url": "https://cdn.example/video.mp4?vid=asset-1",
        }})
    ))
    seen: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        seen["url"] = url
        seen["headers"] = kwargs["headers"]
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4"},
            content=b"\x00\x00\x00\x18ftyp-test",
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    destination = tmp_path / "named-output.mp4"
    try:
        assert client.download_original("asset-1", destination) == destination
    finally:
        client.close()
    assert destination.read_bytes().startswith(b"\x00\x00\x00")
    assert seen["headers"]["referer"] == "https://ads.tiktok.com/"  # type: ignore[index]


def test_billable_network_failure_is_never_safe_to_retry() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("unknown")

    client = SymphonyDirectClient(session=SESSION, transport=httpx.MockTransport(handler))
    with pytest.raises(DirectOutcomeUnknown):
        client.generate_reference_video(
            prompt="x",
            assets=[],
            duration=15,
            model="Dreamina Seedance 2.0",
        )
    client.close()


def test_billable_http_200_structured_rejection_is_definitive() -> None:
    client = SymphonyDirectClient(
        session=SESSION,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"code": 10001114, "message": "User Generation Day Limit"},
            )
        ),
    )
    try:
        with pytest.raises(DirectRejected) as captured:
            client.generate_reference_video(
                prompt="x",
                assets=[],
                duration=15,
                model="Dreamina Seedance 2.0",
            )
    finally:
        client.close()

    assert captured.value.code == 10001114
    assert captured.value.message == "User Generation Day Limit"


def test_missing_session_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.direct_client.load_session", lambda: None)
    with pytest.raises(DirectUnavailable):
        SymphonyDirectClient()
