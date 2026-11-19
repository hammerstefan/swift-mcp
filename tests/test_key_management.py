"""Caller-key management through the operator and MCP HTTP interfaces."""

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time

import pytest
from starlette.testclient import TestClient

from swift_read_only_mcp.server import create_app


class MemoryKeyring:
    """An isolated test adapter for the external Secret Service."""

    def __init__(self):
        self.items = {}

    def put(self, attributes, value):
        key = tuple(sorted(attributes.items()))
        if key in self.items:
            raise ValueError("Secret already exists")
        self.items[key] = value

    def get(self, attributes):
        return self.items.get(tuple(sorted(attributes.items())))


def test_quickstart_initializes_then_reuses_its_key(tmp_path):
    from swift_read_only_mcp.quickstart import caller_attributes, ensure_caller_key

    keyring = MemoryKeyring()
    first = ensure_caller_key(keyring, tmp_path)
    value = keyring.get(caller_attributes(first))
    assert value
    assert ensure_caller_key(keyring, tmp_path) == first
    assert keyring.get(caller_attributes(first)) == value


def test_quickstart_adds_key_to_existing_database_without_replacing_it(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main
    from swift_read_only_mcp.quickstart import caller_attributes, ensure_caller_key

    keyring = MemoryKeyring()
    assert main(["init", "other"], secrets=keyring, state_dir=tmp_path) == 0
    original = json.loads(capsys.readouterr().out)["key_id"]
    quickstart_id = ensure_caller_key(keyring, tmp_path)
    assert quickstart_id != original
    assert keyring.get(caller_attributes(quickstart_id))
    assert keyring.get(
        {"service": "swift-mcp", "purpose": "caller", "workshop": "other", "key-id": str(original)}
    )


def test_quickstart_refuses_multiple_active_keys_and_missing_caller_secret(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main
    from swift_read_only_mcp.quickstart import WORKSHOP, caller_attributes, ensure_caller_key

    keyring = MemoryKeyring()
    key_id = ensure_caller_key(keyring, tmp_path)
    assert main(["rotate", WORKSHOP, str(key_id)], secrets=keyring, state_dir=tmp_path) == 0
    capsys.readouterr()
    with pytest.raises(ValueError, match="Multiple active"):
        ensure_caller_key(keyring, tmp_path)

    # A missing secret is not treated as a reason to provision a new key.
    state = tmp_path / "other-state"
    state.mkdir(mode=0o700)
    other_ring = MemoryKeyring()
    other_id = ensure_caller_key(other_ring, state)
    del other_ring.items[tuple(sorted(caller_attributes(other_id).items()))]
    with pytest.raises(ValueError, match="unavailable"):
        ensure_caller_key(other_ring, state)


def test_quickstart_refuses_orphaned_pepper(tmp_path):
    from swift_read_only_mcp.quickstart import ensure_caller_key

    keyring = MemoryKeyring()
    keyring.put({"service": "swift-mcp", "purpose": "pepper"}, "existing")
    with pytest.raises(ValueError, match="inspect local state"):
        ensure_caller_key(keyring, tmp_path)
    assert not (tmp_path / "keys.db").exists()


def list_tools(client, key):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/list",
            "X-API-Key": key,
        },
    )


def test_initialize_provisions_an_authenticated_workshop_without_printing_secrets(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    keyring = MemoryKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["workshop"] == "validation"
    key_id = str(result["key_id"])
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    key = keyring.get(
        {"service": "swift-mcp", "purpose": "caller", "workshop": "validation", "key-id": key_id}
    )
    assert pepper and key
    assert pepper not in output.out + output.err
    assert key not in output.out + output.err

    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = list_tools(client, key)
    assert response.status_code == 200
    assert '"name":"get_account_stats"' in response.text


def test_revoking_a_compromised_workshop_key_denies_it_immediately(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    keyring = MemoryKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    key_id = json.loads(capsys.readouterr().out)["key_id"]
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    key = keyring.get(
        {
            "service": "swift-mcp",
            "purpose": "caller",
            "workshop": "validation",
            "key-id": str(key_id),
        }
    )
    assert pepper and key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert main(["revoke", "validation", str(key_id)], secrets=keyring, state_dir=tmp_path) == 0
        response = list_tools(client, key)
    assert response.status_code == 401


def test_adding_a_second_workshop_keeps_the_first_key_usable(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    keyring = MemoryKeyring()
    assert main(["init", "first"], secrets=keyring, state_dir=tmp_path) == 0
    first_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["add", "second"], secrets=keyring, state_dir=tmp_path) == 0
    second_id = json.loads(capsys.readouterr().out)["key_id"]
    assert first_id != second_id
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    keys = [
        keyring.get(
            {
                "service": "swift-mcp",
                "purpose": "caller",
                "workshop": name,
                "key-id": str(key_id),
            }
        )
        for name, key_id in (("first", first_id), ("second", second_id))
    ]
    assert pepper and all(keys) and keys[0] != keys[1]
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        statuses = [list_tools(client, key).status_code for key in keys]
    assert statuses == [200, 200]


def test_rotation_expires_old_key_24_hours_after_replacement_is_verified(
    tmp_path, capsys, monkeypatch
):
    from swift_read_only_mcp.key_management import main

    now = [1_800_000_000]
    monkeypatch.setattr(time, "time", lambda: now[0])
    keyring = MemoryKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    old_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["rotate", "validation", str(old_id)], secrets=keyring, state_dir=tmp_path) == 0
    new_id = json.loads(capsys.readouterr().out)["key_id"]
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    old_key, new_key = [
        keyring.get(
            {
                "service": "swift-mcp",
                "purpose": "caller",
                "workshop": "validation",
                "key-id": str(key_id),
            }
        )
        for key_id in (old_id, new_id)
    ]
    assert pepper and old_key and new_key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert list_tools(client, old_key).status_code == 200
        assert list_tools(client, new_key).status_code == 200
        assert (
            main(
                ["confirm", "validation", str(new_id), "--client-verified"],
                secrets=keyring,
                state_dir=tmp_path,
                verify=lambda key: list_tools(client, key).status_code == 200,
            )
            == 0
        )
        now[0] += 86_399
        assert list_tools(client, old_key).status_code == 200
        now[0] += 2
        assert list_tools(client, old_key).status_code == 401
        assert list_tools(client, new_key).status_code == 200


def test_failed_replacement_verification_does_not_expire_the_old_key(tmp_path, capsys, monkeypatch):
    from swift_read_only_mcp.key_management import main

    now = [1_800_000_000]
    monkeypatch.setattr(time, "time", lambda: now[0])
    keyring = MemoryKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    old_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["rotate", "validation", str(old_id)], secrets=keyring, state_dir=tmp_path) == 0
    new_id = json.loads(capsys.readouterr().out)["key_id"]
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    old_key = keyring.get(
        {
            "service": "swift-mcp",
            "purpose": "caller",
            "workshop": "validation",
            "key-id": str(old_id),
        }
    )
    assert pepper and old_key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert (
            main(
                ["confirm", "validation", str(new_id), "--client-verified"],
                secrets=keyring,
                state_dir=tmp_path,
                verify=lambda _: False,
            )
            != 0
        )
        now[0] += 86_401
        assert list_tools(client, old_key).status_code == 200


def test_rotation_cannot_be_confirmed_without_verified_client(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    keyring = MemoryKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    old_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["rotate", "validation", str(old_id)], secrets=keyring, state_dir=tmp_path) == 0
    new_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["confirm", "validation", str(new_id)], secrets=keyring, state_dir=tmp_path) != 0


def test_concurrent_operator_fails_without_changing_keys(tmp_path):
    from swift_read_only_mcp.key_management import main

    keyring = MemoryKeyring()
    fd = os.open(tmp_path / "management.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) != 0
        assert keyring.get({"service": "swift-mcp", "purpose": "pepper"}) is None
        assert not (tmp_path / "keys.db").exists()
    finally:
        os.close(fd)


def test_failed_keyring_delivery_revokes_the_new_key_but_keeps_the_old_one(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    class FailingKeyring(MemoryKeyring):
        def put(self, attributes, value):
            super().put(attributes, value)
            if attributes.get("workshop") == "second":
                raise OSError("simulated secret service failure")

    keyring = FailingKeyring()
    assert main(["init", "first"], secrets=keyring, state_dir=tmp_path) == 0
    old_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["add", "second"], secrets=keyring, state_dir=tmp_path) != 0
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    old_key = keyring.get(
        {"service": "swift-mcp", "purpose": "caller", "workshop": "first", "key-id": str(old_id)}
    )
    failed_key = keyring.get(
        {"service": "swift-mcp", "purpose": "caller", "workshop": "second", "key-id": "2"}
    )
    assert pepper and old_key and failed_key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert list_tools(client, old_key).status_code == 200
        assert list_tools(client, failed_key).status_code == 401


def test_unconfirmed_replacement_expires_without_affecting_the_old_key(
    tmp_path, capsys, monkeypatch
):
    from swift_read_only_mcp.key_management import main

    now = [1_800_000_000]
    monkeypatch.setattr(time, "time", lambda: now[0])
    keyring = MemoryKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    old_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["rotate", "validation", str(old_id)], secrets=keyring, state_dir=tmp_path) == 0
    new_id = json.loads(capsys.readouterr().out)["key_id"]
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    old_key, new_key = [
        keyring.get(
            {
                "service": "swift-mcp",
                "purpose": "caller",
                "workshop": "validation",
                "key-id": str(key_id),
            }
        )
        for key_id in (old_id, new_id)
    ]
    assert pepper and old_key and new_key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        now[0] += 86_401
        assert list_tools(client, old_key).status_code == 200
        assert list_tools(client, new_key).status_code == 401


def test_interrupted_new_workshop_key_expires_without_affecting_existing_key(
    tmp_path, capsys, monkeypatch
):
    from swift_read_only_mcp.key_management import main

    class InterruptedKeyring(MemoryKeyring):
        def put(self, attributes, value):
            super().put(attributes, value)
            if attributes.get("workshop") == "second":
                raise SystemExit("simulated process termination")

    now = [1_800_000_000]
    monkeypatch.setattr(time, "time", lambda: now[0])
    keyring = InterruptedKeyring()
    assert main(["init", "first"], secrets=keyring, state_dir=tmp_path) == 0
    old_id = json.loads(capsys.readouterr().out)["key_id"]
    with pytest.raises(SystemExit):
        main(["add", "second"], secrets=keyring, state_dir=tmp_path)
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    old_key = keyring.get(
        {"service": "swift-mcp", "purpose": "caller", "workshop": "first", "key-id": str(old_id)}
    )
    pending_key = keyring.get(
        {"service": "swift-mcp", "purpose": "caller", "workshop": "second", "key-id": "2"}
    )
    assert pepper and old_key and pending_key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        now[0] += 86_401
        assert list_tools(client, old_key).status_code == 200
        assert list_tools(client, pending_key).status_code == 401


def test_status_lists_key_ids_without_returning_secrets(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    keyring = MemoryKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    old_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["rotate", "validation", str(old_id)], secrets=keyring, state_dir=tmp_path) == 0
    new_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["status", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    output = capsys.readouterr()
    entries = json.loads(output.out)["keys"]
    assert [entry["key_id"] for entry in entries] == [old_id, new_id]
    assert entries[1]["rotation_of"] == old_id
    for key_id in (old_id, new_id):
        value = keyring.get(
            {
                "service": "swift-mcp",
                "purpose": "caller",
                "workshop": "validation",
                "key-id": str(key_id),
            }
        )
        assert value and value not in output.out + output.err


def test_emergency_revocation_works_when_keyring_is_locked(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    class LockableKeyring(MemoryKeyring):
        locked = False

        def get(self, attributes):
            if self.locked:
                raise OSError("keyring is locked")
            return super().get(attributes)

    keyring = LockableKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    key_id = json.loads(capsys.readouterr().out)["key_id"]
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    key = keyring.get(
        {
            "service": "swift-mcp",
            "purpose": "caller",
            "workshop": "validation",
            "key-id": str(key_id),
        }
    )
    assert pepper and key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    keyring.locked = True
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert main(["revoke", "validation", str(key_id)], secrets=keyring, state_dir=tmp_path) == 0
        assert list_tools(client, key).status_code == 401


@pytest.mark.skipif(
    not all(
        shutil.which(name) for name in ("dbus-run-session", "gnome-keyring-daemon", "secret-tool")
    ),
    reason="isolated GNOME Keyring tools are unavailable",
)
def test_cli_secrets_are_readable_by_secret_tool_in_an_isolated_keyring(tmp_path):
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
        "HOME": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_RUNTIME_DIR": str(tmp_path),
        "OUTER_DBUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
    }
    subprocess.run(
        ["dbus-run-session", "--", sys.executable, "tests/fixtures/keyring_scenario.py"],
        env=environment,
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_rotation_does_not_report_success_if_keyring_loses_replacement(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    class DroppingKeyring(MemoryKeyring):
        def put(self, attributes, value):
            if attributes.get("key-id") == "2":
                return
            super().put(attributes, value)

    keyring = DroppingKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    key_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["rotate", "validation", str(key_id)], secrets=keyring, state_dir=tmp_path) != 0


def test_new_key_can_be_issued_after_the_last_key_is_revoked(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    keyring = MemoryKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    old_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["revoke", "validation", str(old_id)], secrets=keyring, state_dir=tmp_path) == 0
    capsys.readouterr()
    assert main(["add", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    new_id = json.loads(capsys.readouterr().out)["key_id"]
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    old_key, new_key = [
        keyring.get(
            {
                "service": "swift-mcp",
                "purpose": "caller",
                "workshop": "validation",
                "key-id": str(key_id),
            }
        )
        for key_id in (old_id, new_id)
    ]
    assert pepper and old_key and new_key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert list_tools(client, old_key).status_code == 401
        assert list_tools(client, new_key).status_code == 200


def test_wrong_pepper_fails_closed_before_starting_the_server(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    keyring = MemoryKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    capsys.readouterr()
    keyring.items[tuple(sorted({"service": "swift-mcp", "purpose": "pepper"}.items()))] = (
        "wrong-test-pepper"
    )
    assert main(["start", "validation"], secrets=keyring, state_dir=tmp_path) != 0


def test_insecure_state_directory_is_rejected_before_creating_secrets(tmp_path):
    from swift_read_only_mcp.key_management import main

    tmp_path.chmod(0o755)
    keyring = MemoryKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) != 0
    assert keyring.get({"service": "swift-mcp", "purpose": "pepper"}) is None
    assert not (tmp_path / "keys.db").exists()


def test_key_is_not_authorized_before_secret_delivery_succeeds(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    class InterruptedKeyring(MemoryKeyring):
        client = None
        observed_status = None

        def put(self, attributes, value):
            if attributes.get("workshop") == "second":
                assert self.client is not None
                self.observed_status = list_tools(self.client, value).status_code
                raise SystemExit("simulated interruption during secret delivery")
            super().put(attributes, value)

    keyring = InterruptedKeyring()
    assert main(["init", "first"], secrets=keyring, state_dir=tmp_path) == 0
    capsys.readouterr()
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    assert pepper
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        keyring.client = client
        with pytest.raises(SystemExit):
            main(["add", "second"], secrets=keyring, state_dir=tmp_path)
    assert keyring.observed_status == 401


def test_interrupted_delivery_can_be_recovered_only_from_a_matching_secret(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    class InterruptedKeyring(MemoryKeyring):
        interrupted = False

        def put(self, attributes, value):
            super().put(attributes, value)
            if attributes.get("workshop") == "second" and not self.interrupted:
                self.interrupted = True
                raise SystemExit("simulated interruption after delivery")

    keyring = InterruptedKeyring()
    assert main(["init", "first"], secrets=keyring, state_dir=tmp_path) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["add", "second"], secrets=keyring, state_dir=tmp_path)
    assert main(["recover", "second", "2"], secrets=keyring, state_dir=tmp_path) == 0
    pepper = keyring.get({"service": "swift-mcp", "purpose": "pepper"})
    key = keyring.get(
        {"service": "swift-mcp", "purpose": "caller", "workshop": "second", "key-id": "2"}
    )
    assert pepper and key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(tmp_path / "keys.db"),
            "SWIFT_MCP_KEY_PEPPER": pepper,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert list_tools(client, key).status_code == 200


def test_recovery_does_not_activate_a_second_key_for_the_same_workshop(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    class InterruptedKeyring(MemoryKeyring):
        interrupted = False

        def put(self, attributes, value):
            super().put(attributes, value)
            if attributes.get("workshop") == "second" and not self.interrupted:
                self.interrupted = True
                raise SystemExit("simulated interruption")

    keyring = InterruptedKeyring()
    assert main(["init", "first"], secrets=keyring, state_dir=tmp_path) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["add", "second"], secrets=keyring, state_dir=tmp_path)
    assert main(["add", "second"], secrets=keyring, state_dir=tmp_path) == 0
    capsys.readouterr()
    assert main(["recover", "second", "2"], secrets=keyring, state_dir=tmp_path) != 0


def test_unconfirmed_replacement_cannot_be_rotated_again(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    keyring = MemoryKeyring()
    assert main(["init", "validation"], secrets=keyring, state_dir=tmp_path) == 0
    old_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["rotate", "validation", str(old_id)], secrets=keyring, state_dir=tmp_path) == 0
    new_id = json.loads(capsys.readouterr().out)["key_id"]
    assert main(["rotate", "validation", str(new_id)], secrets=keyring, state_dir=tmp_path) != 0


def test_revoking_an_interrupted_key_prevents_later_recovery(tmp_path, capsys):
    from swift_read_only_mcp.key_management import main

    class InterruptedKeyring(MemoryKeyring):
        def put(self, attributes, value):
            super().put(attributes, value)
            if attributes.get("workshop") == "second":
                raise SystemExit("simulated interruption")

    keyring = InterruptedKeyring()
    assert main(["init", "first"], secrets=keyring, state_dir=tmp_path) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["add", "second"], secrets=keyring, state_dir=tmp_path)
    assert main(["revoke", "second", "2"], secrets=keyring, state_dir=tmp_path) == 0
    assert main(["recover", "second", "2"], secrets=keyring, state_dir=tmp_path) != 0
