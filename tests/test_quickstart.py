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
