from __future__ import annotations

import json
import re
import sys
from typing import Any, Protocol

import keyring

SERVICE = "tiktok-symphony-proxy"
ACCOUNT = "direct-api-session"
INDEX_ACCOUNT = f"{ACCOUNT}-index"
# Windows Credential Manager stores text as UTF-16 and caps one generic
# credential blob at 2560 bytes. Leave generous room for backend overhead.
CHUNK_SIZE = 800


class PasswordStore(Protocol):
    def get_password(self, service: str, account: str) -> str | None: ...
    def set_password(self, service: str, account: str, password: str) -> None: ...


def _validate_main_account(main_account: str | None) -> str | None:
    if main_account is None:
        return None
    value = str(main_account).strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value):
        raise ValueError(
            "main account alias must be 1-64 characters using letters, numbers, dot, underscore or dash"
        )
    return value


def _credential_base(main_account: str | None) -> str:
    alias = _validate_main_account(main_account)
    return ACCOUNT if alias is None else f"{ACCOUNT}@{alias}"


def _load_index(store: PasswordStore) -> dict[str, Any]:
    raw = store.get_password(SERVICE, INDEX_ACCOUNT)
    if not raw:
        return {"version": 1, "main_accounts": {}}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("stored Symphony main-account index is invalid") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("stored Symphony main-account index has an unsupported format")
    if not isinstance(value.get("main_accounts"), dict):
        raise ValueError("stored Symphony main-account index is incomplete")
    return value


def _save_index(index: dict[str, Any], store: PasswordStore) -> None:
    store.set_password(
        SERVICE,
        INDEX_ACCOUNT,
        json.dumps(index, ensure_ascii=True, separators=(",", ":")),
    )


def _public_metadata(session: dict[str, Any]) -> dict[str, Any]:
    accounts = session.get("accounts")
    rows = accounts if isinstance(accounts, list) else []
    return {
        "browser_profile": session.get("browser_profile"),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at") or session.get("created_at"),
        "account_count": len(rows),
        "accounts": [
            {"name": str(row.get("name") or ""), "id": str(row.get("id") or "")}
            for row in rows
            if isinstance(row, dict) and row.get("name") and row.get("id")
        ],
    }


def save_session(
    session: dict[str, Any],
    store: PasswordStore = keyring,
    *,
    main_account: str | None = None,
) -> None:
    if session.get("version") != 1 or not session.get("cookies"):
        raise ValueError("invalid Symphony direct session")
    alias = _validate_main_account(main_account)
    base = _credential_base(alias)
    raw = json.dumps(session, ensure_ascii=True, separators=(",", ":"))
    chunks = [raw[index:index + CHUNK_SIZE] for index in range(0, len(raw), CHUNK_SIZE)]
    for index, chunk in enumerate(chunks):
        store.set_password(SERVICE, f"{base}-{index:03d}", chunk)
    store.set_password(SERVICE, base, json.dumps({"chunks": len(chunks)}))
    if alias is not None:
        index = _load_index(store)
        index["main_accounts"][alias] = _public_metadata(session)
        _save_index(index, store)


def load_session(
    store: PasswordStore = keyring,
    *,
    main_account: str | None = None,
) -> dict[str, Any] | None:
    base = _credential_base(main_account)
    metadata = store.get_password(SERVICE, base)
    if not metadata:
        return None
    try:
        count = int(json.loads(metadata)["chunks"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("stored Symphony direct session metadata is invalid") from exc
    parts = [store.get_password(SERVICE, f"{base}-{index:03d}") for index in range(count)]
    if any(part is None for part in parts):
        raise ValueError("stored Symphony direct session is incomplete")
    raw = "".join(part or "" for part in parts)
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("stored Symphony direct session has an unsupported format")
    return value


def list_main_accounts(store: PasswordStore = keyring) -> list[dict[str, Any]]:
    index = _load_index(store)
    return [
        {"main_account": alias, **metadata}
        for alias, metadata in index["main_accounts"].items()
        if isinstance(metadata, dict)
    ]


def migrate_legacy_session(
    main_account: str,
    *,
    browser_profile: str | None = None,
    store: PasswordStore = keyring,
) -> dict[str, Any]:
    session = load_session(store)
    if session is None:
        raise ValueError("legacy Symphony direct session does not exist")
    migrated = dict(session)
    migrated["main_account"] = _validate_main_account(main_account)
    if browser_profile:
        migrated["browser_profile"] = _validate_main_account(browser_profile)
    save_session(migrated, store, main_account=main_account)
    return _public_metadata(migrated)


def _main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: python -m app.session_store save|migrate-legacy|list [options]",
            file=sys.stderr,
        )
        return 2
    command = sys.argv[1]
    args = sys.argv[2:]

    def option(name: str, *, required: bool = False) -> str | None:
        if name not in args:
            if required:
                raise ValueError(f"missing required option {name}")
            return None
        index = args.index(name)
        if index + 1 >= len(args):
            raise ValueError(f"missing value for {name}")
        return args[index + 1]

    if command == "save":
        alias = option("--main-account")
        browser_profile = option("--browser-profile")
        session = json.load(sys.stdin)
        if alias:
            session["main_account"] = alias
        if browser_profile:
            session["browser_profile"] = browser_profile
        save_session(session, main_account=alias)
        print(json.dumps({"ok": True, "main_account": alias}))
    elif command == "migrate-legacy":
        alias = option("--main-account", required=True)
        metadata = migrate_legacy_session(
            str(alias),
            browser_profile=option("--browser-profile"),
        )
        print(json.dumps({"ok": True, "main_account": alias, **metadata}))
    elif command == "list":
        print(json.dumps({"ok": True, "data": list_main_accounts()}))
    else:
        print(f"unknown command: {command}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
