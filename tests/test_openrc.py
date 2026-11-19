"""OpenStack RC credentials are parsed as data, not executed as shell code."""

import os

import pytest

from swift_read_only_mcp.config import load_settings

EXAMPLE_RC = """\
NOVARC_CONFIG_VERSION=1
NOVARC=$(readlink -f "${BASH_SOURCE:-${0}}" 2>/dev/null) ||
    NOVARC=$(python -c 'print("ignored")')
NOVA_KEY_DIR=${NOVARC%/*}
export OS_USERNAME="swift-user"
export OS_TENANT_NAME="swift-project"
export OS_PASSWORD='fixture-$password'
export OS_AUTH_URL="https://identity.example/v3"
export OS_REGION_NAME="test-region"
export NOVA_USERNAME="${OS_USERNAME}"
export OS_PROJECT_NAME=${OS_TENANT_NAME}
export OS_PROJECT_DOMAIN_NAME=Default
export OS_USER_DOMAIN_NAME=Default
export OS_IDENTITY_API_VERSION=3
export OS_INTERFACE=public
export OS_STORAGE_URL=https://storage.example/v1/AUTH_test
"""


def settings_for(tmp_path, text, **overrides):
    rc = tmp_path / "credentials.rc"
    rc.write_text(text, encoding="utf-8")
    database = tmp_path / "keys.db"
    database.touch()
    return load_settings(
        {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "fixture-pepper",
            "SWIFT_MCP_OPENRC": str(rc),
            **overrides,
        }
    )


def test_example_rc_loads_password_scope_region_interface_and_storage(tmp_path):
    settings = settings_for(tmp_path, EXAMPLE_RC)
    assert settings.swift.user_name == "swift-user"
    assert settings.swift.password == "fixture-$password"
    assert settings.swift.auth_url == "https://identity.example/v3"
    assert settings.swift.storage_url == "https://storage.example/v1/AUTH_test"
    assert settings.swift.keystone_options == {
        "user_domain_name": "Default",
        "project_name": "swift-project",
        "project_domain_name": "Default",
        "region_name": "test-region",
        "endpoint_type": "public",
    }


def test_references_and_escaping_in_double_quoted_values(tmp_path):
    settings = settings_for(
        tmp_path,
        'export OS_TENANT_NAME="test"\n'
        'export OS_AUTH_TOKEN="prefix-${OS_TENANT_NAME}-\\$literal"\n'
        'export OS_STORAGE_URL="https://storage.example/v1/AUTH_$OS_TENANT_NAME"\n',
    )
    assert settings.swift.token == "prefix-test-$literal"
    assert settings.swift.storage_url == "https://storage.example/v1/AUTH_test"


def test_rc_credentials_do_not_mix_with_ambient_authentication(tmp_path):
    settings = settings_for(
        tmp_path,
        EXAMPLE_RC,
        OS_AUTH_TOKEN="ambient-token",
        OS_USER_ID="ambient-user",
        OS_PROJECT_ID="ambient-project",
        ST_AUTH_VERSION="2",
    )
    assert settings.swift.token is None
    assert settings.swift.user_name == "swift-user"
    assert "user_id" not in settings.swift.keystone_options
    assert "project_id" not in settings.swift.keystone_options


def test_incomplete_rc_does_not_fall_back_to_ambient_credentials(tmp_path):
    with pytest.raises(ValueError, match="Swift authentication is not configured"):
        settings_for(tmp_path, 'export OS_USERNAME="swift-user"', OS_AUTH_TOKEN="ambient-token")


def test_rc_accepts_token_mode_and_quoted_literals(tmp_path):
    settings = settings_for(
        tmp_path,
        "export OS_AUTH_TOKEN='quoted-$literal;`literal`'\n"
        'export OS_STORAGE_URL="https://storage.example/v1/AUTH_test" # account\n',
    )
    assert settings.swift.token == "quoted-$literal;`literal`"


@pytest.mark.parametrize(
    "assignment",
    [
        'export OS_PASSWORD="$(touch /tmp/never)"',
        "export OS_PASSWORD=`touch /tmp/never`",
        "export OS_PASSWORD=password;touch /tmp/never",
        "export OS_PASSWORD=${OS_USERNAME:-fallback}",
        "export OS_PASSWORD=$MISSING",
        'export OS_PASSWORD="unterminated',
        "export OS_PASSWORD=password extra",
        "export OS_PASSWORD =password",
        "export OS_UNSUPPORTED=value",
        "export OS_PASSWORD=secret\nexport OS_PASSWORD=other",
        "export OS_PASSWORD=secret\x00",
    ],
)
def test_invalid_assignments_fail_closed_without_exposing_values(tmp_path, assignment):
    with pytest.raises(ValueError, match="Invalid Swift RC assignment") as error:
        settings_for(tmp_path, assignment)
    assert "secret" not in str(error.value)


def test_non_os_shell_is_not_executed(tmp_path):
    marker = tmp_path / "not-created"
    settings_for(tmp_path, f"IGNORED=$(touch {marker})\n" + EXAMPLE_RC)
    assert not marker.exists()


def test_variable_expansion_is_bounded(tmp_path):
    text = "OS_TENANT_NAME=" + "a" * 32000 + "\nOS_PROJECT_NAME=" + "${OS_TENANT_NAME}" * 1000
    with pytest.raises(ValueError, match="Invalid Swift RC assignment"):
        settings_for(tmp_path, text)


def test_rc_url_still_requires_safe_endpoint(tmp_path):
    with pytest.raises(ValueError, match="Invalid Swift endpoint URL"):
        settings_for(
            tmp_path, EXAMPLE_RC.replace("https://storage.example", "http://storage.example")
        )


def test_mixed_credential_modes_in_rc_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="Swift authentication"):
        settings_for(tmp_path, EXAMPLE_RC + "export OS_AUTH_TOKEN=token\n")


@pytest.mark.parametrize("content", [b"\xff", b"#" * (64 * 1024 + 1)])
def test_invalid_or_oversized_file_is_rejected(tmp_path, content):
    rc = tmp_path / "credentials.rc"
    rc.write_bytes(content)
    with pytest.raises(ValueError, match="Invalid Swift RC file"):
        load_settings({"SWIFT_MCP_OPENRC": str(rc)})


def test_missing_or_non_regular_file_is_rejected(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    for path in (tmp_path / "missing", tmp_path, fifo):
        with pytest.raises(ValueError, match="Invalid Swift RC file"):
            load_settings({"SWIFT_MCP_OPENRC": str(path)})
