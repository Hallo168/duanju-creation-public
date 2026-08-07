from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from .session_store import load_session, save_session

BASE_URL = "https://ads.tiktok.com"
COMMON_QUERY = {
    "aid": "585599",
    "app_name": "creative_aio_client",
    "device_platform": "web",
}
MODEL_IDS = {
    "Dreamina Seedance 2.0": "2000004",
}


class DirectUnavailable(RuntimeError):
    """The direct path is unavailable before a billable request is sent."""


class DirectOutcomeUnknown(RuntimeError):
    """A billable request may have reached Symphony; never auto-retry it."""


class DirectRejected(RuntimeError):
    """Symphony deliberately rejected the request; nothing was created or charged.

    Distinguished from DirectOutcomeUnknown by evidence: a structured JSON body with a
    non-zero application `code` proves the request reached the app layer and was refused
    there (e.g. 10001303 content-guidelines). Network errors, 5xx and empty bodies stay
    "unknown" — those really are ambiguous. Without this split one rejected unit aborts
    an entire batch under the V20 stop-and-verify rule.
    """

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"Symphony rejected the request: code={code}, message={message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CachedAsset:
    name: str
    url: str


class AssetCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def digest(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def resolve(self, paths: list[str]) -> list[CachedAsset]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DirectUnavailable("direct asset cache is not initialized") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise DirectUnavailable(f"direct asset cache is unreadable: {exc}") from exc

        assets: list[CachedAsset] = []
        missing: list[str] = []
        for item in paths:
            path = Path(item)
            digest = self.digest(path)
            cached = raw.get(digest)
            if not isinstance(cached, dict) or not cached.get("url"):
                missing.append(str(path))
                continue
            assets.append(CachedAsset(name=path.name, url=str(cached["url"])))
        if missing:
            raise DirectUnavailable(f"{len(missing)} reference image(s) are not in the direct asset cache")
        return assets


class SymphonyDirectClient:
    def __init__(
        self,
        *,
        session: dict[str, Any] | None = None,
        main_account: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        value = session or (
            load_session(main_account=main_account)
            if main_account is not None
            else load_session()
        )
        if not value:
            suffix = f" for main account {main_account!r}" if main_account else ""
            raise DirectUnavailable(f"direct session is not initialized{suffix}")
        self.main_account = main_account or value.get("main_account")
        self.session = value
        cookies = httpx.Cookies()
        cookie_count = 0
        for cookie in value.get("cookies", []):
            if not cookie.get("name") or not cookie.get("value"):
                continue
            cookies.set(
                str(cookie["name"]),
                str(cookie["value"]),
                domain=str(cookie.get("domain") or "ads.tiktok.com"),
                path=str(cookie.get("path") or "/"),
            )
            cookie_count += 1
        if not cookie_count:
            raise DirectUnavailable("direct session contains no cookies")
        self.client = httpx.Client(
            base_url=BASE_URL,
            cookies=cookies,
            headers={
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "referer": f"{BASE_URL}/creative/creativestudio/image-to-video",
                "origin": BASE_URL,
                "x-creative-source": value.get("creative_source", "CreativeStudio/MiniApp/ImageToVideo"),
                "x-csrftoken": value.get("csrf", ""),
                "x-fp-id": value.get("fp_id", ""),
                "agw-js-conv": "str",
                **({"user-agent": value["user_agent"]} if value.get("user_agent") else {}),
                **({"sec-ch-ua": value["sec_ch_ua"]} if value.get("sec_ch_ua") else {}),
                **({"sec-ch-ua-mobile": value["sec_ch_ua_mobile"]} if value.get("sec_ch_ua_mobile") else {}),
                **({"sec-ch-ua-platform": value["sec_ch_ua_platform"]} if value.get("sec_ch_ua_platform") else {}),
            },
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self.client.close()

    def _url(self, path: str) -> str:
        device_id = str(self.session.get("device_id", ""))
        query = {
            **COMMON_QUERY,
            **({"did": device_id, "device_id": device_id} if device_id else {}),
        }
        return f"{path}?{urlencode(query)}"

    def _post(self, path: str, body: Any = None, *, billable: bool = False) -> dict[str, Any]:
        try:
            response = self.client.post(self._url(path), json=body)
        except httpx.RequestError as exc:
            error = DirectOutcomeUnknown if billable else DirectUnavailable
            raise error(f"Symphony direct request failed: {exc}") from exc
        if response.status_code in {401, 403}:
            raise DirectUnavailable("Symphony direct session expired")
        if response.status_code != 200:
            # 结构化的 code+message ⇒ 平台已处理并明确拒绝，任务没建、钱没扣，是确定性结果。
            # 只有这种情况才降级为 DirectRejected；5xx／空体／非 JSON 一律仍按"结果未知"处理。
            try:
                payload = response.json()
                rejected_code = int(payload.get("code") or 0)
            except (ValueError, TypeError):
                payload, rejected_code = None, 0
            if rejected_code:
                raise DirectRejected(
                    rejected_code,
                    str(payload.get("message") or payload.get("msg") or "no message"),
                )
            error = DirectOutcomeUnknown if billable else DirectUnavailable
            # 带上响应体：只有状态码的话，平台为什么拒绝无从查起（26-U14 的 400 就卡在这）
            detail = response.text[:400].replace("\n", " ") if response.text else "empty body"
            raise error(f"Symphony direct API returned HTTP {response.status_code}: {detail}")
        try:
            data = response.json()
        except ValueError as exc:
            error = DirectOutcomeUnknown if billable else DirectUnavailable
            raise error("Symphony direct API returned non-JSON data") from exc
        if data.get("code") != 0:
            message = data.get("message") or data.get("msg") or data.get("status_msg") or "no message"
            if billable:
                # A structured non-zero response is definitive even when the
                # platform transports it as HTTP 200: no task, no charge.
                raise DirectRejected(int(data.get("code") or 0), str(message))
            raise DirectUnavailable(
                f"Symphony direct API rejected the request: code={data.get('code')}, message={message}"
            )
        return data

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        device_id = str(self.session.get("device_id", ""))
        query = {**COMMON_QUERY, "did": device_id, "device_id": device_id, **params}
        try:
            response = self.client.get(path, params=query)
        except httpx.RequestError as exc:
            raise DirectUnavailable(f"Symphony direct request failed: {exc}") from exc
        if response.status_code in {401, 403}:
            raise DirectUnavailable("Symphony direct session expired")
        if response.status_code != 200:
            raise DirectUnavailable(f"Symphony direct API returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise DirectUnavailable("Symphony direct API returned non-JSON data") from exc
        if data.get("code") != 0:
            raise DirectUnavailable(f"Symphony direct API rejected the request: code={data.get('code')}")
        return data

    def _creative_get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        device_id = str(self.session.get("device_id", ""))
        query = {
            **COMMON_QUERY,
            **({"did": device_id, "device_id": device_id} if device_id else {}),
            **(params or {}),
        }
        try:
            response = self.client.get(path, params=query)
        except httpx.RequestError as exc:
            raise DirectUnavailable(f"Symphony account request failed: {exc}") from exc
        if response.status_code in {401, 403}:
            raise DirectUnavailable("Symphony direct session expired")
        if response.status_code != 200:
            raise DirectUnavailable(f"Symphony account API returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise DirectUnavailable("Symphony account API returned non-JSON data") from exc
        base = data.get("BaseResp") or {}
        if base.get("StatusCode") != 0:
            raise DirectUnavailable(
                "Symphony account API rejected the request: "
                f"code={base.get('StatusCode')}, message={base.get('StatusMessage') or 'no message'}"
            )
        return data

    def _creative_post(self, path: str, body: Any = None) -> dict[str, Any]:
        try:
            response = self.client.post(self._url(path), json=body)
        except httpx.RequestError as exc:
            raise DirectUnavailable(f"Symphony account request failed: {exc}") from exc
        if response.status_code in {401, 403}:
            raise DirectUnavailable("Symphony direct session expired")
        if response.status_code != 200:
            raise DirectUnavailable(f"Symphony account API returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise DirectUnavailable("Symphony account API returned non-JSON data") from exc
        base = data.get("BaseResp") or {}
        if base.get("StatusCode") != 0:
            raise DirectUnavailable(
                "Symphony account API rejected the request: "
                f"code={base.get('StatusCode')}, message={base.get('StatusMessage') or 'no message'}"
            )
        return data

    def generating_count(self) -> int:
        data = self._post("/creative_bff_i18n/api/cue/generating-task-count")
        return int(data.get("data", {}).get("total", 0))

    def account_directory(self) -> list[dict[str, str]]:
        """Return selectable accounts without querying the active account."""
        data = self._creative_get("/CreativeOne/Client/ClientGetAccountList")
        accounts = []
        for item in data.get("accounts") or []:
            account_id = str(item.get("aioClientID") or "")
            name = str(item.get("profileName") or "")
            if not account_id or not name:
                continue
            accounts.append({"name": name, "id": account_id})
        if not accounts:
            raise DirectUnavailable("Symphony returned no selectable sub-accounts")
        return accounts

    def list_accounts(self, *, persist: bool = False) -> list[dict[str, Any]]:
        active = self.current_account()
        accounts = self.account_directory()
        rows = [
            {**account, "active": account["id"] == active["id"]}
            for account in accounts
        ]
        if persist and self.main_account:
            self.session["accounts"] = accounts
            self.session["active_account"] = active
            self.session["updated_at"] = datetime.now(UTC).isoformat()
            save_session(self.session, main_account=str(self.main_account))
        return rows

    def current_account(self) -> dict[str, str]:
        data = self._creative_get("/CreativeOne/Client/ClientGetAccountInfo")
        item = data.get("account") or {}
        account_id = str(item.get("aioClientID") or "")
        name = str(item.get("profileName") or "")
        if not account_id or not name:
            raise DirectUnavailable("Symphony returned no active sub-account")
        return {"name": name, "id": account_id}

    def select_account(self, target: str, *, persist: bool = False) -> dict[str, str]:
        want = str(target).strip()
        accounts = self.account_directory()
        selected = next(
            (item for item in accounts if item["id"] == want or item["name"] == want),
            None,
        )
        if selected is None:
            names = ", ".join(item["name"] for item in accounts)
            raise DirectUnavailable(f"no Symphony sub-account {want!r}; available: {names}")
        return self.select_known_account(selected, persist=persist)

    def select_known_account(
        self,
        selected: dict[str, str],
        *,
        persist: bool = False,
    ) -> dict[str, str]:
        """Select an account from a freshly fetched directory and verify it.

        Pool inspection obtains the directory once, then gives each independent
        client one known record. This avoids re-fetching the whole directory in
        every worker without weakening the post-switch identity check.
        """
        account_id = str(selected.get("id") or "").strip()
        account_name = str(selected.get("name") or "").strip()
        if not account_id or not account_name:
            raise DirectUnavailable("invalid Symphony account directory entry")
        cookie_rows = [
            cookie for cookie in self.session.get("cookies", [])
            if cookie.get("name") == "s_aio_client_id"
        ]
        if not cookie_rows:
            raise DirectUnavailable("direct session has no Symphony account selector cookie")
        previous = [(row, str(row.get("value") or "")) for row in cookie_rows]
        try:
            for row, _old_value in previous:
                row["value"] = account_id
                self.client.cookies.set(
                    "s_aio_client_id",
                    account_id,
                    domain=str(row.get("domain") or ".tiktok.com"),
                    path=str(row.get("path") or "/"),
                )
            verified = self.current_account()
            if verified["id"] != account_id:
                raise DirectUnavailable(
                    f"Symphony account verification failed: requested {account_name}, got {verified['name']}"
                )
            if persist:
                save_session(
                    self.session,
                    main_account=str(self.main_account) if self.main_account else None,
                )
            return verified
        except Exception:
            for row, old_value in previous:
                row["value"] = old_value
                self.client.cookies.set(
                    "s_aio_client_id",
                    old_value,
                    domain=str(row.get("domain") or ".tiktok.com"),
                    path=str(row.get("path") or "/"),
                )
            raise

    def credits_for_verified_account(self, account: dict[str, str]) -> dict[str, Any]:
        """Query credits after select_known_account verified this same client."""
        data = self._creative_post("/CreativeOne/SymphonyPlatform/QueryCreditAccount", {})
        return {
            **account,
            "credits": int(data.get("credits") or 0),
            "weeklySpent": int(data.get("weekly_spent") or 0),
        }

    def credits(self) -> dict[str, Any]:
        data = self._creative_post("/CreativeOne/SymphonyPlatform/QueryCreditAccount", {})
        account = self.current_account()
        return {
            **account,
            "credits": int(data.get("credits") or 0),
            "weeklySpent": int(data.get("weekly_spent") or 0),
        }

    def check_task(self, task_id: str) -> dict[str, Any]:
        data = self._post(
            "/creative_bff_i18n/api/cue/generate-task/check",
            {"taskId": str(task_id)},
        )
        drafts = data.get("data", {}).get("draft_infos") or []
        if not drafts:
            raise DirectUnavailable(f"Symphony returned no draft for task {task_id}")
        draft = next(
            (item for item in drafts if str(item.get("taskId", "")) == str(task_id)),
            drafts[0],
        )
        return draft

    def original_video_url(self, asset_id: str) -> str:
        data = self._get(
            "/creative_bff_i18n/api/cue/watermark_video_info",
            {"vid": str(asset_id)},
        )
        url = data.get("data", {}).get("downloadable_url") or data.get("data", {}).get("origin_url")
        if not isinstance(url, str) or not url.startswith("https://") or f"vid={asset_id}" not in url:
            raise DirectUnavailable(f"Symphony returned no verified original URL for {asset_id}")
        return url

    def download_original(self, asset_id: str, out: Path) -> Path:
        url = self.original_video_url(asset_id)
        try:
            # The CDN rejects an otherwise valid signed URL without an ads.tiktok.com
            # Referer. Use the captured browser UA as well, but never persist the URL.
            response = httpx.get(
                url,
                headers={
                    "referer": f"{BASE_URL}/",
                    "user-agent": self.session.get("user_agent", "Mozilla/5.0"),
                    "accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
                },
                timeout=120.0,
                follow_redirects=True,
            )
        except httpx.RequestError as exc:
            raise DirectUnavailable(f"original video download failed: {exc}") from exc
        if response.status_code != 200 or not response.content:
            raise DirectUnavailable(f"original video download returned HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "").lower()
        if "video" not in content_type and not response.content.startswith(b"\x00\x00\x00"):
            raise DirectUnavailable(f"original video download returned {content_type or 'unknown content type'}")
        destination = out if out.suffix.lower() == ".mp4" else out / f"{asset_id}.mp4"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination

    def generate_reference_video(
        self,
        *,
        prompt: str,
        assets: list[CachedAsset],
        duration: int,
        model: str,
    ) -> dict[str, Any]:
        model_id = MODEL_IDS.get(model)
        if not model_id:
            raise DirectUnavailable(f"direct mode does not support model {model!r}")
        images = []
        mentions = []
        urls = []
        for index, asset in enumerate(assets, start=1):
            item = {
                "id": str(uuid4()),
                "name": asset.name,
                "previewUrl": asset.url,
                "fileType": "image",
                "label": f"image {index}",
            }
            images.append(item)
            mentions.append({"type": 1, "id": asset.url})
            urls.append(asset.url)
        settings = {
            "images": images,
            "prompt": prompt,
            "aiModel": model_id,
            "duration": duration,
        }
        payload = {
            "image": "",
            "images": urls,
            "prompt": prompt,
            "duration": duration,
            "model": model_id,
            # The browser sends this nested object as a JSON-encoded string.
            # Sending an object returns Symphony code 10001200.
            "settings": json.dumps(settings, ensure_ascii=False, separators=(",", ":")),
            "mentions": mentions,
        }
        return self._post(
            "/creative_bff_i18n/api/cue/i2v/gen_r2v_video",
            payload,
            billable=True,
        )
