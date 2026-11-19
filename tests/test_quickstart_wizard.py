"""Exercise the wizard against fake local commands, never a real Workshop or browser."""

import json
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "scripts/quickstart-wizard.sh"
CALLER_SECRET = "quickstart-test-secret-never-print"

FAKE_COMMAND = r"""#!/usr/bin/python3
import json
import os
import signal
import sys
import time
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
state = Path(os.environ["FAKE_STATE"])

def event(command, **fields):
    with (state / "events").open("a") as out:
        out.write(json.dumps({"command": command, **fields}) + "\n")

if name == "uv":
    event("uv", args=args)
    target = Path(os.environ["UV_PROJECT_ENVIRONMENT"]) / "bin"
    target.mkdir(parents=True)
    (target / "python").symlink_to(os.environ["FAKE_COMMAND"])
elif name == "python":
    if args[1] == "swift_read_only_mcp.key_management":
        event("server")
        print("Uvicorn running on http://127.0.0.1:8000", file=sys.stderr, flush=True)
        if os.environ.get("SERVER_EXITS"):
            time.sleep(0.5)
            sys.exit(1)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        while True:
            time.sleep(0.05)
    action = args[2]
    event("helper-" + action, rc=os.environ.get("SWIFT_MCP_OPENRC"),
          containers=os.environ.get("SWIFT_MCP_CONTAINERS"))
    if (action == "token-status" and os.environ.get("TOKEN_MISSING")
            and not (state / "token").exists()):
        sys.exit(3)
    if action == "store-token":
        if not sys.stdin.read().startswith("github_pat_"):
            sys.exit(1)
        (state / "token").write_text("stored")
    if action == "validate" and os.environ.get("INVALID_CONFIG"):
        sys.exit(1)
    if action == "ensure-key":
        print("42")
    if action == "caller-key":
        print(os.environ["FAKE_CALLER_SECRET"])
elif name == "workshop":
    if args == ["--version"]:
        event("workshop-version", has_swift=any(
            k.startswith(("OS_", "ST_", "SWIFT_MCP_", "SWIFTCLIENT_")) for k in os.environ
        ))
        print(os.environ.get("WORKSHOP_VERSION", "0.9.9"))
        sys.exit(0)
    project = Path(args[1])
    command = args[2]
    event("workshop-" + command, project=str(project), args=args[3:],
          has_swift=any(k.startswith(("OS_", "ST_", "SWIFT_MCP_", "SWIFTCLIENT_"))
                        for k in os.environ),
          has_key=bool(os.environ.get("COPILOT_MCP_SWIFT_KEY")))
    if command == "list":
        existing = project == Path(os.environ["CHECKOUT_PROJECT"])
        ready = (existing and os.environ.get("EXISTING")) or (project / "launched").exists()
        status = "Ready" if ready else "Off"
        print("swift-mcp-quickstart  " + status + "  -")
    elif command == "launch":
        (project / "launched").write_text("yes")
        if os.environ.get("LAUNCH_FAIL"):
            sys.exit(1)
    elif command == "connect" and os.environ.get("CONNECT_FAIL") and "github-token" in args[3]:
        sys.exit(1)
    elif command == "run":
        if not os.environ.get("COPILOT_MCP_SWIFT_KEY"):
            sys.exit(2)
        if os.environ.get("SERVER_EXITS") or os.environ.get("RUN_WAIT"):
            time.sleep(10)
    elif command == "remove":
        if os.environ.get("REMOVE_FAIL"):
            sys.exit(1)
        (project / "launched").unlink(missing_ok=True)
elif name == "xdg-open":
    event("browser", url=args[0])
else:
    sys.exit(2)
"""


@pytest.fixture
def fake_environment(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "fake-command"
    fake.write_text(FAKE_COMMAND)
    fake.chmod(0o755)
    for name in ("uv", "workshop", "xdg-open"):
        (bin_dir / name).symlink_to(fake)
    state = tmp_path / "state"
    state.mkdir()
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(temporary),
        "FAKE_STATE": str(state),
        "FAKE_COMMAND": str(fake),
        "FAKE_CALLER_SECRET": CALLER_SECRET,
        "CHECKOUT_PROJECT": str(ROOT / "quickstart"),
        "SWIFT_MCP_OPENRC": "/protected/test.rc",
        "SWIFT_MCP_CONTAINERS": '["test"]',
        "OS_PASSWORD": "private-swift-password",
    }
    return env, state, temporary


def run_wizard(env, input_text="\ny\n"):
    return subprocess.run(
        ["bash", str(WIZARD)],
        input=input_text,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def events(state):
    path = state / "events"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_one_shot_cleans_only_its_temporary_session(fake_environment):
    env, state, temporary = fake_environment
    result = run_wizard(env)
    assert result.returncode == 0, result.stderr
    logged = events(state)
    commands = [event["command"] for event in logged]
    assert commands.index("workshop-launch") < commands.index("workshop-run")
    assert commands.index("workshop-run") < commands.index("workshop-remove")
    assert commands.count("workshop-disconnect") == 2
    workshop_events = [event for event in logged if "project" in event]
    assert all(not event["has_swift"] for event in workshop_events)
    assert next(event for event in workshop_events if event["command"] == "workshop-run")["has_key"]
    assert all(event["project"] != str(ROOT / "quickstart") for event in workshop_events[1:])
    assert list(temporary.iterdir()) == []
    assert CALLER_SECRET not in result.stdout + result.stderr
    assert "private-swift-password" not in result.stdout + result.stderr


def test_missing_swift_settings_are_prompted_and_not_persisted(fake_environment):
    env, state, temporary = fake_environment
    del env["SWIFT_MCP_OPENRC"]
    del env["SWIFT_MCP_CONTAINERS"]
    result = run_wizard(env, '\ny\n/protected/prompted.rc\n["prompted"]\n')
    assert result.returncode == 0, result.stderr
    validation = next(event for event in events(state) if event["command"] == "helper-validate")
    assert validation["rc"] == "/protected/prompted.rc"
    assert validation["containers"] == '["prompted"]'
    assert list(temporary.iterdir()) == []


def test_missing_token_opens_form_and_stores_it_in_keyring(fake_environment):
    env, state, _ = fake_environment
    env["TOKEN_MISSING"] = "1"
    result = run_wizard(env, "\ny\ngithub_pat_example\n")
    assert result.returncode == 0, result.stderr
    logged = events(state)
    browser = next(event for event in logged if event["command"] == "browser")
    assert browser["url"].startswith("https://github.com/settings/personal-access-tokens/new?")
    assert any(event["command"] == "helper-store-token" for event in logged)
    assert "github_pat_example" not in result.stdout + result.stderr


def test_existing_workshop_is_not_touched(fake_environment):
    env, state, temporary = fake_environment
    env["EXISTING"] = "1"
    result = run_wizard(env)
    assert result.returncode != 0
    assert [event["command"] for event in events(state)] == ["workshop-version", "workshop-list"]
    assert list(temporary.iterdir()) == []


def test_unsupported_workshop_version_fails_before_launch(fake_environment):
    env, state, temporary = fake_environment
    env["WORKSHOP_VERSION"] = "0.9.7"
    result = run_wizard(env)
    assert result.returncode != 0
    assert "Workshop 0.9.8 or later" in result.stderr
    assert [event["command"] for event in events(state)] == ["workshop-version"]
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize("failure", ["LAUNCH_FAIL", "CONNECT_FAIL", "SERVER_EXITS"])
def test_partial_or_failed_sessions_are_cleaned(fake_environment, failure):
    env, state, temporary = fake_environment
    env[failure] = "1"
    result = run_wizard(env)
    assert result.returncode != 0
    assert "workshop-remove" in [event["command"] for event in events(state)]
    assert list(temporary.iterdir()) == []


def test_failed_removal_preserves_session_for_recovery(fake_environment):
    env, state, temporary = fake_environment
    env["REMOVE_FAIL"] = "1"
    result = run_wizard(env)
    assert result.returncode != 0
    assert "Cleanup incomplete" in result.stderr
    assert len(list(temporary.iterdir())) == 1
    assert "workshop-remove" in [event["command"] for event in events(state)]


def test_sigterm_stops_the_client_and_removes_its_workshop(fake_environment):
    env, state, temporary = fake_environment
    env["RUN_WAIT"] = "1"
    process = subprocess.Popen(
        ["bash", str(WIZARD)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write("\ny\n")
    process.stdin.flush()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if any(event["command"] == "workshop-run" for event in events(state)):
                break
            if process.poll() is not None:
                pytest.fail("Wizard exited before starting Copilot")
            time.sleep(0.02)
        else:
            pytest.fail("Wizard did not start Copilot")
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 143, (stdout, stderr)
        assert "workshop-remove" in [event["command"] for event in events(state)]
        assert list(temporary.iterdir()) == []
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_invalid_config_stops_before_token_or_workshop_launch(fake_environment):
    env, state, temporary = fake_environment
    env["INVALID_CONFIG"] = "1"
    result = run_wizard(env)
    assert result.returncode != 0
    commands = [event["command"] for event in events(state)]
    assert "helper-token-status" not in commands
    assert "workshop-launch" not in commands
    assert list(temporary.iterdir()) == []
