"""Exercise the manual quickstart without touching the host keyring or Workshop."""

import json
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/quickstart.sh"

UV = r"""#!/usr/bin/python3
import json
import os
import socket
import sys
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])
args = sys.argv[1:]
with (state / "events").open("a") as out:
    out.write(json.dumps(args) + "\n")
if args == ["run", "--locked", "python", "-m", "swift_read_only_mcp.quickstart", "ensure-key"]:
    if os.environ.get("FAKE_KEY_FAILURE"):
        sys.exit(1)
    (state / "key").touch()
    print("42")
elif args == ["run", "--locked", "swift-mcp-keys", "start", "swift-mcp-quickstart"]:
    if not (state / "key").exists():
        print("Key provisioning failed; inspect local state before retrying", file=sys.stderr)
        sys.exit(1)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 8000))
        server.listen()
        while True:
            connection, _ = server.accept()
            connection.close()
else:
    sys.exit(2)
"""

WORKSHOP = r"""#!/usr/bin/python3
import os
import sys
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])
with (state / "workshop").open("a") as out:
    out.write(" ".join(sys.argv[1:]) + "\n")
if any(key.startswith(("OS_", "ST_", "SWIFT_MCP_", "SWIFTCLIENT_")) for key in os.environ):
    sys.exit(2)
# Stop after the server reaches readiness, before any real Workshop work.
sys.exit(1)
"""


@pytest.fixture
def serve_environment(tmp_path):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 8000))
        except OSError:
            pytest.skip("quickstart port 8000 is in use")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in (("uv", UV), ("workshop", WORKSHOP)):
        command = bin_dir / name
        command.write_text(source)
        command.chmod(0o755)
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "FAKE_STATE": str(tmp_path),
        "SWIFT_MCP_OPENRC": "/protected/test.rc",
        "SWIFT_MCP_CONTAINERS": '["cloud-images"]',
        "OS_PASSWORD": "private-test-password",
    }, tmp_path


def run_serve(env):
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=15, check=False
    )


def test_serve_prepares_missing_key_before_starting_server(serve_environment):
    env, state = serve_environment
    result = run_serve(env)
    assert "Key provisioning failed" not in result.stderr
    events = [json.loads(line) for line in (state / "events").read_text().splitlines()]
    assert events == [
        ["run", "--locked", "python", "-m", "swift_read_only_mcp.quickstart", "ensure-key"],
        ["run", "--locked", "swift-mcp-keys", "start", "swift-mcp-quickstart"],
    ]
    assert "caller key ID 42" in result.stdout
    assert (state / "workshop").exists()
    assert "private-test-password" not in result.stdout + result.stderr


def test_serve_does_not_start_server_when_key_setup_fails(serve_environment):
    env, state = serve_environment
    env["FAKE_KEY_FAILURE"] = "1"
    result = run_serve(env)
    events = [json.loads(line) for line in (state / "events").read_text().splitlines()]
    assert result.returncode != 0
    assert events == [
        ["run", "--locked", "python", "-m", "swift_read_only_mcp.quickstart", "ensure-key"]
    ]
    assert not (state / "workshop").exists()
