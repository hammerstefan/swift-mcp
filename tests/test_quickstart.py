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

    def set_secret(self, value):
        self.value = value


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
    monkeypatch.setattr(quickstart, "verify_github_token", lambda _token: 0)
    if expected is None:
        with pytest.raises(ValueError):
            quickstart.copilot_token_status()
    else:
        assert quickstart.copilot_token_status() == expected


@pytest.mark.parametrize(
    ("http_status", "expected"), [(200, 0), (401, 4), (403, 6), (429, 6), (500, 5), (302, 5)]
)
def test_github_token_check_classifies_responses_without_following_redirects(
    monkeypatch, http_status, expected
):
    import httpx

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(http_status, headers={"Location": "https://example.org/"})

    real_client = httpx.Client

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"] == 10.0
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(quickstart.httpx, "Client", client)
    assert quickstart.verify_github_token("github_pat_fixture") == expected
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.github.com/user"
    assert requests[0].headers["authorization"] == "Bearer github_pat_fixture"


def test_github_token_check_handles_transport_failure_without_disclosing_token(monkeypatch, capsys):
    import httpx

    def client(**_kwargs):
        raise httpx.ConnectError("github_pat_fixture")

    monkeypatch.setattr(quickstart.httpx, "Client", client)
    assert quickstart.verify_github_token("github_pat_fixture") == 5
    captured = capsys.readouterr()
    assert "github_pat_fixture" not in captured.out + captured.err


@pytest.mark.parametrize("token", ["", "github_pat_", "github_pat_bad\nvalue", "ghp_classic"])
def test_invalid_token_format_is_rejected_without_a_network_request(monkeypatch, token):
    def client(**_kwargs):
        pytest.fail("Invalid token must not be sent over the network")

    monkeypatch.setattr(quickstart.httpx, "Client", client)
    assert quickstart.verify_github_token(token) == 4


@pytest.mark.parametrize("candidate_status", [4, 5, 6])
def test_replacement_does_not_change_old_token_when_new_token_check_fails(
    monkeypatch, candidate_status
):
    item = Item(b"github_pat_old")
    monkeypatch.setattr(quickstart.secretstorage, "dbus_init", lambda: io.BytesIO())
    monkeypatch.setattr(
        quickstart.secretstorage, "get_collection_by_alias", lambda *_: Collection([item])
    )
    monkeypatch.setattr(quickstart.secretstorage, "search_items", lambda *_: iter([item]))
    monkeypatch.setattr(quickstart, "verify_github_token", lambda _token: candidate_status)

    assert quickstart.save_copilot_token("github_pat_new", replace=True) == candidate_status
    assert item.value == b"github_pat_old"


@pytest.mark.parametrize("old_status", [0, 5, 6])
def test_replacement_refuses_to_overwrite_token_unless_old_token_is_rejected(
    monkeypatch, old_status
):
    item = Item(b"github_pat_old")
    monkeypatch.setattr(quickstart.secretstorage, "dbus_init", lambda: io.BytesIO())
    monkeypatch.setattr(
        quickstart.secretstorage, "get_collection_by_alias", lambda *_: Collection([item])
    )
    monkeypatch.setattr(quickstart.secretstorage, "search_items", lambda *_: iter([item]))
    monkeypatch.setattr(
        quickstart, "verify_github_token", lambda token: 0 if token.endswith("new") else old_status
    )

    assert quickstart.save_copilot_token("github_pat_new", replace=True) != 0
    assert item.value == b"github_pat_old"


def test_replacement_updates_only_single_rejected_quickstart_item(monkeypatch):
    item = Item(b"github_pat_old")
    monkeypatch.setattr(quickstart.secretstorage, "dbus_init", lambda: io.BytesIO())
    monkeypatch.setattr(
        quickstart.secretstorage, "get_collection_by_alias", lambda *_: Collection([item])
    )
    monkeypatch.setattr(quickstart.secretstorage, "search_items", lambda *_: iter([item]))
    monkeypatch.setattr(
        quickstart, "verify_github_token", lambda token: 0 if token.endswith("new") else 4
    )

    assert quickstart.save_copilot_token("github_pat_new", replace=True) == 0
    assert item.value == b"github_pat_new"


def test_replacement_stops_if_token_changes_while_checking(monkeypatch):
    item = Item(b"github_pat_old")
    monkeypatch.setattr(quickstart.secretstorage, "dbus_init", lambda: io.BytesIO())
    monkeypatch.setattr(
        quickstart.secretstorage, "get_collection_by_alias", lambda *_: Collection([item])
    )
    monkeypatch.setattr(quickstart.secretstorage, "search_items", lambda *_: iter([item]))

    def verify(token):
        if token.endswith("old"):
            item.value = b"github_pat_other"
            return 4
        return 0

    monkeypatch.setattr(quickstart, "verify_github_token", verify)
    with pytest.raises(ValueError, match="changed"):
        quickstart.save_copilot_token("github_pat_new", replace=True)
    assert item.value == b"github_pat_other"


def test_replacement_refuses_ambiguous_keyring_items(monkeypatch):
    old, other = Item(b"github_pat_old"), Item(b"github_pat_other")
    monkeypatch.setattr(quickstart.secretstorage, "dbus_init", lambda: io.BytesIO())
    monkeypatch.setattr(
        quickstart.secretstorage, "get_collection_by_alias", lambda *_: Collection([old])
    )
    monkeypatch.setattr(quickstart.secretstorage, "search_items", lambda *_: iter([old, other]))
    monkeypatch.setattr(quickstart, "verify_github_token", lambda _token: 0)
    with pytest.raises(ValueError, match="ambiguous"):
        quickstart.save_copilot_token("github_pat_new", replace=True)
    assert old.value == b"github_pat_old"
    assert other.value == b"github_pat_other"


def test_token_cli_checks_rejected_saved_token_and_replaces_it(monkeypatch, capsys):
    import httpx

    item = Item(b"github_pat_old")
    monkeypatch.setattr(quickstart.secretstorage, "dbus_init", lambda: io.BytesIO())
    monkeypatch.setattr(
        quickstart.secretstorage, "get_collection_by_alias", lambda *_: Collection([item])
    )
    monkeypatch.setattr(quickstart.secretstorage, "search_items", lambda *_: iter([item]))
    real_client = httpx.Client

    def respond(request):
        status = 200 if request.headers["authorization"].endswith("new") else 401
        return httpx.Response(status)

    monkeypatch.setattr(
        quickstart.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(sys, "argv", ["quickstart", "token-status"])
    assert quickstart.main() == 4
    monkeypatch.setattr(sys, "argv", ["quickstart", "replace-token"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("github_pat_new"))
    assert quickstart.main() == 0
    assert item.value == b"github_pat_new"
    monkeypatch.setattr(sys, "argv", ["quickstart", "token-status"])
    assert quickstart.main() == 0
    output = capsys.readouterr()
    assert "github_pat_" not in output.out + output.err


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
