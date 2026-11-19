"""Validate quickstart input and keyring checks without using the host keyring."""

import io
import sys

import pytest

from swift_read_only_mcp import quickstart


class Item:
    def __init__(self, value=b"token", locked=False):
        self.value = value
        self.locked = locked
        self.item_path = "/fake/item"

    def is_locked(self):
        return self.locked

    def get_secret(self):
        return self.value


class Collection:
    def __init__(self, items, locked=False):
        self.items = items
        self.locked = locked

    def is_locked(self):
        return self.locked

    def search_items(self, _attributes):
        return iter(self.items)


@pytest.mark.parametrize(
    ("all_items", "default_items", "locked", "expected"),
    [
        ([], [], False, 3),
        ([Item()], [Item()], False, 0),
        ([Item()], [], False, None),
        ([Item(), Item()], [Item()], False, None),
        ([Item()], [Item(b"")], False, None),
        ([Item()], [Item(locked=True)], False, None),
        ([], [], True, None),
    ],
)
def test_token_lookup_requires_one_unlocked_default_collection_item(
    monkeypatch, all_items, default_items, locked, expected
):
    monkeypatch.setattr(quickstart.secretstorage, "dbus_init", lambda: io.BytesIO())
    monkeypatch.setattr(
        quickstart.secretstorage,
        "get_collection_by_alias",
        lambda *_: Collection(default_items, locked),
    )
    monkeypatch.setattr(quickstart.secretstorage, "search_items", lambda *_: iter(all_items))
    if expected is None:
        with pytest.raises(ValueError):
            quickstart.copilot_token_status()
    else:
        assert quickstart.copilot_token_status() == expected


def test_validate_config_requires_protected_rc_and_valid_allowlist(monkeypatch, tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    rc = tmp_path / "swift.rc"
    rc.write_text("OS_AUTH_TOKEN='test-token'\nOS_STORAGE_URL='https://storage.example/v1/test'\n")
    rc.chmod(0o600)
    monkeypatch.setattr(
        quickstart.os,
        "environ",
        {"SWIFT_MCP_OPENRC": str(rc), "SWIFT_MCP_CONTAINERS": '["test"]'},
    )
    quickstart.validate_config(root)

    rc.chmod(0o644)
    with pytest.raises(ValueError, match="Protect"):
        quickstart.validate_config(root)
    rc.chmod(0o600)
    monkeypatch.setitem(quickstart.os.environ, "SWIFT_MCP_CONTAINERS", '["invalid/name"]')
    with pytest.raises(ValueError, match="allowlist"):
        quickstart.validate_config(root)
    monkeypatch.setitem(quickstart.os.environ, "SWIFT_MCP_CONTAINERS", '["test"]')
    monkeypatch.setitem(quickstart.os.environ, "SWIFTCLIENT_INSECURE", "true")
    with pytest.raises(ValueError, match="TLS"):
        quickstart.validate_config(root)
    monkeypatch.delitem(quickstart.os.environ, "SWIFTCLIENT_INSECURE")
    with pytest.raises(ValueError, match="outside"):
        quickstart.validate_config(tmp_path)


def test_validate_config_expands_home_before_enforcing_rc_protection(monkeypatch, tmp_path):
    home = tmp_path / "home with spaces"
    root = home / "checkout"
    root.mkdir(parents=True)
    rc = home / "swift.rc"
    rc.write_text("OS_AUTH_TOKEN='test-token'\nOS_STORAGE_URL='https://storage.example/v1/test'\n")
    rc.chmod(0o600)
    monkeypatch.setattr(
        quickstart.os,
        "environ",
        {"HOME": str(home), "SWIFT_MCP_OPENRC": "~/swift.rc", "SWIFT_MCP_CONTAINERS": '["test"]'},
    )
    quickstart.validate_config(root)

    rc.chmod(0o644)
    with pytest.raises(ValueError, match="Protect"):
        quickstart.validate_config(root)
    rc.chmod(0o600)
    with pytest.raises(ValueError, match="outside"):
        quickstart.validate_config(home)

    inside = root / "inside.rc"
    inside.write_text(rc.read_text())
    inside.chmod(0o600)
    (home / "link.rc").symlink_to(inside)
    monkeypatch.setitem(quickstart.os.environ, "SWIFT_MCP_OPENRC", "~/link.rc")
    with pytest.raises(ValueError, match="outside"):
        quickstart.validate_config(root)


def test_validate_cli_explains_rc_restrictions_without_printing_secrets(
    monkeypatch, tmp_path, capsys
):
    root = tmp_path / "checkout"
    root.mkdir()
    rc = root / "example_creds.rc"
    rc.write_text("OS_AUTH_TOKEN='private-token'\n")
    rc.chmod(0o664)
    monkeypatch.setattr(
        quickstart.os,
        "environ",
        {"SWIFT_MCP_OPENRC": str(rc), "SWIFT_MCP_CONTAINERS": '["cloud-images"]'},
    )
    monkeypatch.setattr(sys, "argv", ["quickstart", "validate", str(root)])

    assert quickstart.main() == 1
    output = capsys.readouterr()
    assert "outside the checkout" in output.err
    assert "owner-only" in output.err
    assert "private-token" not in output.out + output.err


def test_validate_cli_reports_invalid_allowlist_without_printing_rc(monkeypatch, tmp_path, capsys):
    root = tmp_path / "checkout"
    root.mkdir()
    rc = tmp_path / "swift.rc"
    rc.write_text(
        "OS_AUTH_TOKEN='private-token'\nOS_STORAGE_URL='https://storage.example/v1/test'\n"
    )
    rc.chmod(0o600)
    monkeypatch.setattr(
        quickstart.os,
        "environ",
        {"SWIFT_MCP_OPENRC": str(rc), "SWIFT_MCP_CONTAINERS": '["invalid/name"]'},
    )
    monkeypatch.setattr(sys, "argv", ["quickstart", "validate", str(root)])

    assert quickstart.main() == 1
    output = capsys.readouterr()
    assert "SWIFT_MCP_CONTAINERS" in output.err
    assert "private-token" not in output.out + output.err


def test_validate_cli_never_prints_unrecognized_exception_details(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["quickstart", "validate", "/checkout"])

    def fail(_root):
        raise ValueError("private-token")

    monkeypatch.setattr(quickstart, "validate_config", fail)
    assert quickstart.main() == 1
    output = capsys.readouterr()
    assert "private-token" not in output.out + output.err
    assert "Quickstart check failed" in output.err


@pytest.mark.parametrize(
    ("failure", "description"),
    [
        ("Protect the RC file and keep it outside the checkout", "outside the checkout"),
        ("Invalid Swift RC file", "missing, unreadable, or not a valid regular file"),
        ("Invalid Swift RC assignment", "invalid or unsupported assignment"),
        ("Invalid container allowlist", "JSON array of valid container names"),
        ("Swift authentication is not configured", "complete supported credentials"),
        ("Swift TLS verification must remain enabled", "TLS verification must remain enabled"),
        ("Invalid Swift endpoint URL", "invalid or insecure endpoint URL"),
        ("Invalid Swift TLS configuration", "TLS certificate configuration is invalid"),
        ("Invalid Swift client certificate settings", "OS_KEY requires an OS_CERT"),
        ("Invalid page limit", "SWIFT_MCP_PAGE_LIMIT must be a positive integer"),
        ("Invalid object byte limit", "SWIFT_MCP_BYTE_LIMIT must be a positive integer"),
        ("Invalid listing byte limit", "SWIFT_MCP_LISTING_BYTE_LIMIT must be a positive integer"),
        ("Invalid caller rate limit", "SWIFT_MCP_RATE_LIMIT must be a positive integer"),
    ],
)
def test_validate_cli_explains_each_known_configuration_failure(
    monkeypatch, capsys, failure, description
):
    monkeypatch.setattr(sys, "argv", ["quickstart", "validate", "/checkout"])

    def fail(_root):
        raise ValueError(failure)

    monkeypatch.setattr(quickstart, "validate_config", fail)
    assert quickstart.main() == 1
    output = capsys.readouterr()
    assert description in output.err


@pytest.mark.parametrize(
    ("setting", "description"),
    [
        ("SWIFT_MCP_PAGE_LIMIT", "SWIFT_MCP_PAGE_LIMIT must be a positive integer"),
        ("SWIFT_MCP_BYTE_LIMIT", "SWIFT_MCP_BYTE_LIMIT must be a positive integer"),
        ("SWIFT_MCP_LISTING_BYTE_LIMIT", "SWIFT_MCP_LISTING_BYTE_LIMIT must be a positive integer"),
        ("SWIFT_MCP_RATE_LIMIT", "SWIFT_MCP_RATE_LIMIT must be a positive integer"),
    ],
)
def test_validate_cli_identifies_invalid_limit(monkeypatch, tmp_path, capsys, setting, description):
    root = tmp_path / "checkout"
    root.mkdir()
    rc = tmp_path / "swift.rc"
    rc.write_text(
        "OS_AUTH_TOKEN='private-token'\nOS_STORAGE_URL='https://storage.example/v1/test'\n"
    )
    rc.chmod(0o600)
    monkeypatch.setattr(
        quickstart.os,
        "environ",
        {"SWIFT_MCP_OPENRC": str(rc), "SWIFT_MCP_CONTAINERS": '["test"]', setting: "0"},
    )
    monkeypatch.setattr(sys, "argv", ["quickstart", "validate", str(root)])
    assert quickstart.main() == 1
    output = capsys.readouterr()
    assert description in output.err
    assert "private-token" not in output.out + output.err


def test_validate_cli_identifies_client_key_without_certificate(monkeypatch, tmp_path, capsys):
    root = tmp_path / "checkout"
    root.mkdir()
    rc = tmp_path / "swift.rc"
    rc.write_text(
        "OS_AUTH_TOKEN='private-token'\nOS_STORAGE_URL='https://storage.example/v1/test'\n"
        "OS_KEY='/private/client.key'\n"
    )
    rc.chmod(0o600)
    monkeypatch.setattr(
        quickstart.os,
        "environ",
        {"SWIFT_MCP_OPENRC": str(rc), "SWIFT_MCP_CONTAINERS": '["test"]'},
    )
    monkeypatch.setattr(sys, "argv", ["quickstart", "validate", str(root)])
    assert quickstart.main() == 1
    output = capsys.readouterr()
    assert "OS_KEY requires an OS_CERT" in output.err
    assert "private-token" not in output.out + output.err
    assert "/private/client.key" not in output.out + output.err


@pytest.mark.parametrize(
    ("rc_content", "extra_env", "description"),
    [
        (
            "OS_AUTH_TOKEN='private-token'\nOS_AUTH_TOKEN='duplicate'\n",
            {},
            "invalid or unsupported assignment",
        ),
        ("OS_AUTH_TOKEN='private-token'\n", {}, "complete supported credentials"),
        (
            "OS_AUTH_TOKEN='private-token'\nOS_STORAGE_URL='http://storage.example/v1/test'\n",
            {},
            "invalid or insecure endpoint URL",
        ),
        (
            "OS_AUTH_TOKEN='private-token'\nOS_STORAGE_URL='https://storage.example/v1/test'\n"
            "OS_CACERT='/missing/ca.pem'\n",
            {},
            "TLS certificate configuration is invalid",
        ),
        (
            "OS_AUTH_TOKEN='private-token'\nOS_STORAGE_URL='https://storage.example/v1/test'\n",
            {"SWIFTCLIENT_INSECURE": "true"},
            "TLS verification must remain enabled",
        ),
    ],
)
def test_validate_cli_reports_actual_swift_configuration_failures(
    monkeypatch, tmp_path, capsys, rc_content, extra_env, description
):
    root = tmp_path / "checkout"
    root.mkdir()
    rc = tmp_path / "swift.rc"
    rc.write_text(rc_content)
    rc.chmod(0o600)
    monkeypatch.setattr(
        quickstart.os,
        "environ",
        {"SWIFT_MCP_OPENRC": str(rc), "SWIFT_MCP_CONTAINERS": '["test"]', **extra_env},
    )
    monkeypatch.setattr(sys, "argv", ["quickstart", "validate", str(root)])
    assert quickstart.main() == 1
    output = capsys.readouterr()
    assert description in output.err
    assert "private-token" not in output.out + output.err
