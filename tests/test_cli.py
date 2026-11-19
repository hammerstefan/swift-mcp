"""The process entry point fails closed without MCP caller credentials."""

import os
import subprocess
import sys


def test_server_refuses_to_listen_without_a_key_store(tmp_path):
    environment = {
        **os.environ,
        "SWIFT_MCP_KEY_DB": str(tmp_path / "absent.db"),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
    }
    result = subprocess.run(
        [sys.executable, "-m", "swift_read_only_mcp"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode != 0
    assert "fixture-swift-token" not in result.stderr
    assert "private-password" not in result.stderr
    assert "MCP caller authentication is not configured" in result.stderr


def test_corrupt_caller_store_fails_with_safe_diagnostic(tmp_path):
    database = tmp_path / "corrupt.db"
    database.write_bytes(b"not a SQLite database")
    result = subprocess.run(
        [sys.executable, "-m", "swift_read_only_mcp"],
        env={
            **os.environ,
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode != 0
    assert result.stderr.strip() == "MCP caller authentication is not configured"
