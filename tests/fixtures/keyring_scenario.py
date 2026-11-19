"""Exercise the operator CLI against an isolated D-Bus and GNOME Keyring."""

import json
import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx
import secretstorage
from jeepney import DBusAddress, new_method_call


def main():
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS") == os.environ.get("OUTER_DBUS_ADDRESS"):
        raise RuntimeError("D-Bus session is not isolated")
    daemon = subprocess.Popen(
        ["gnome-keyring-daemon", "--foreground", "--unlock", "--components=secrets"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert daemon.stdin is not None
        daemon.stdin.write(b"test-only-keyring-password")
        daemon.stdin.close()
        deadline = time.monotonic() + 10
        bus = DBusAddress(
            "/org/freedesktop/DBus",
            bus_name="org.freedesktop.DBus",
            interface="org.freedesktop.DBus",
        )
        while True:
            try:
                with closing(secretstorage.dbus_init()) as connection:
                    message = new_method_call(
                        bus, "NameHasOwner", "s", ("org.freedesktop.secrets",)
                    )
                    if connection.send_and_get_reply(message).body[0]:
                        collection = secretstorage.get_collection_by_alias(connection, "default")
                        if not collection.is_locked():
                            break
            except Exception:
                pass
            if daemon.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Isolated keyring did not start")
            time.sleep(0.05)

        result = subprocess.run(
            [sys.executable, "-m", "swift_read_only_mcp.key_management", "init", "validation"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        key_id = str(json.loads(result.stdout)["key_id"])
        item = subprocess.run(
            [
                "secret-tool",
                "lookup",
                "service",
                "swift-mcp",
                "purpose",
                "caller",
                "workshop",
                "validation",
                "key-id",
                key_id,
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
        assert item.stdout.startswith(b"smcp-")
        assert item.stdout.decode() not in result.stdout + result.stderr
        assert (Path.home() / ".local/state/swift-mcp/keys.db").is_file()
        environment = {
            **os.environ,
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
        }
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        environment["SWIFT_MCP_PORT"] = str(port)
        server = subprocess.Popen(
            [sys.executable, "-m", "swift_read_only_mcp.key_management", "start", "validation"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Isolated MCP server did not start") from None
                    time.sleep(0.05)
            response = httpx.post(
                f"http://127.0.0.1:{port}/mcp",
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
                    "X-API-Key": item.stdout.decode(),
                },
                timeout=5,
                trust_env=False,
            )
            assert response.status_code == 200
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "swift_read_only_mcp.key_management",
                    "revoke",
                    "validation",
                    key_id,
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            denied = httpx.post(
                f"http://127.0.0.1:{port}/mcp",
                headers={"X-API-Key": item.stdout.decode()},
                timeout=5,
                trust_env=False,
            )
            assert denied.status_code == 401
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait(timeout=5)


if __name__ == "__main__":
    main()
