from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app.main import create_app
from app.licensing import (
    LICENSE_PREFIX,
    PRODUCT_ID,
    LicenseError,
    LicenseManager,
    _b64url_encode,
    canonical_json,
    machine_code,
    parse_machine_code,
)
from app import integrity
from app.integrity import compiled_bundle_root, verify_manifest


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.values[(service, account)] = password


MACHINE = "a1" * 32
NOW = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)


def signed_token(
    private: Ed25519PrivateKey,
    **overrides: Any,
) -> str:
    payload = {
        "version": 1,
        "license_id": str(uuid4()),
        "product": PRODUCT_ID,
        "machine": MACHINE,
        "edition": "standard",
        "issued_at": NOW.isoformat().replace("+00:00", "Z"),
        "not_before": NOW.isoformat().replace("+00:00", "Z"),
        "expires_at": (NOW + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
        "max_version": "0.1.0",
        "features": ["generate", "download", "accounts"],
        "nonce": "test-nonce",
        **overrides,
    }
    body = canonical_json(payload)
    return f"{LICENSE_PREFIX}.{_b64url_encode(body)}.{_b64url_encode(private.sign(body))}"


def manager(
    private: Ed25519PrivateKey,
    *,
    now: datetime = NOW,
    machine: str = MACHINE,
    store: MemoryStore | None = None,
) -> LicenseManager:
    return LicenseManager(
        private.public_key(),
        store=store or MemoryStore(),
        clock=lambda: now,
        machine_hash=machine,
    )


def test_machine_code_round_trip_and_checksum() -> None:
    code = machine_code(MACHINE)
    assert parse_machine_code(code) == MACHINE
    with pytest.raises(LicenseError, match="校验"):
        parse_machine_code(code[:-1] + ("0" if code[-1] != "0" else "1"))


def test_valid_license_activation_is_stored_and_feature_checked() -> None:
    private = Ed25519PrivateKey.generate()
    store = MemoryStore()
    licensing = manager(private, store=store)
    token = signed_token(private)
    verified = licensing.activate(token)
    assert verified.payload["machine"] == MACHINE
    assert licensing.verify_installed("generate").payload["edition"] == "standard"
    assert licensing.installed_token() == token


def test_license_tampering_is_rejected() -> None:
    private = Ed25519PrivateKey.generate()
    token = signed_token(private)
    prefix, payload, signature = token.split(".")
    decoded = json.loads(__import__("base64").urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded["edition"] = "unlimited"
    forged = f"{prefix}.{_b64url_encode(canonical_json(decoded))}.{signature}"
    with pytest.raises(LicenseError) as exc:
        manager(private).verify(forged)
    assert exc.value.code == "license_signature_invalid"


@pytest.mark.parametrize(
    ("overrides", "now", "machine", "feature", "code"),
    [
        ({}, NOW, "b2" * 32, None, "license_wrong_machine"),
        ({"expires_at": (NOW - timedelta(seconds=1)).isoformat()}, NOW, MACHINE, None, "license_expired"),
        ({}, NOW, MACHINE, "admin", "license_feature_denied"),
        ({"max_version": "0.0.9"}, NOW, MACHINE, None, "license_version_denied"),
    ],
)
def test_license_policy_rejections(
    overrides: dict[str, Any],
    now: datetime,
    machine: str,
    feature: str | None,
    code: str,
) -> None:
    private = Ed25519PrivateKey.generate()
    with pytest.raises(LicenseError) as exc:
        manager(private, now=now, machine=machine).verify(
            signed_token(private, **overrides),
            feature=feature,
        )
    assert exc.value.code == code


def test_clock_rollback_is_detected() -> None:
    private = Ed25519PrivateKey.generate()
    store = MemoryStore()
    later = manager(private, now=NOW + timedelta(hours=1), store=store)
    token = signed_token(private)
    later.activate(token)
    earlier = manager(private, now=NOW, store=store)
    with pytest.raises(LicenseError) as exc:
        earlier.verify_installed()
    assert exc.value.code == "clock_rollback"


def test_signed_integrity_manifest_detects_modified_file(tmp_path) -> None:
    private = Ed25519PrivateKey.generate()
    target = tmp_path / "runtime" / "worker.bin"
    target.parent.mkdir()
    target.write_bytes(b"original")
    payload = {
        "version": 1,
        "product": PRODUCT_ID,
        "created_at": "2026-07-29T08:00:00Z",
        "files": {
            "runtime/worker.bin": {
                "sha256": hashlib.sha256(b"original").hexdigest(),
                "size": 8,
            },
        },
    }
    (tmp_path / "integrity.json").write_text(json.dumps({
        "payload": payload,
        "signature": _b64url_encode(private.sign(canonical_json(payload))),
    }), encoding="utf-8")
    assert verify_manifest(tmp_path, private.public_key())["product"] == PRODUCT_ID
    target.write_bytes(b"tampered")
    with pytest.raises(LicenseError) as exc:
        verify_manifest(tmp_path, private.public_key())
    assert exc.value.code == "integrity_hash_mismatch"


def test_integrity_manifest_missing_fails_closed(tmp_path) -> None:
    private = Ed25519PrivateKey.generate()

    with pytest.raises(LicenseError) as exc:
        verify_manifest(tmp_path, private.public_key())

    assert exc.value.code == "integrity_manifest_invalid"


def test_compiled_bundle_root_does_not_guess_when_manifest_is_missing(
    monkeypatch,
    tmp_path,
) -> None:
    executable = tmp_path / "runtime" / "proxy" / "SymphonyLocal.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setitem(integrity.__dict__, "__compiled__", True)
    monkeypatch.setattr(integrity.sys, "executable", str(executable))

    with pytest.raises(LicenseError) as exc:
        compiled_bundle_root()

    assert exc.value.code == "integrity_manifest_missing"


def test_api_blocks_work_until_signed_license_is_activated() -> None:
    private = Ed25519PrivateKey.generate()
    licensing = manager(private)

    class NeverRunner:
        def run(self, _argv: list[str], _timeout: int) -> None:
            raise AssertionError("license middleware must block before invoking work")

    client = TestClient(create_app(
        runner=NeverRunner(),  # type: ignore[arg-type]
        license_manager=licensing,
    ))
    blocked = client.get("/v1/jobs")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "license_required"

    activation = client.post("/v1/license/activate", json={
        "license": signed_token(private),
    })
    assert activation.status_code == 200
    assert activation.json()["data"]["valid"] is True
