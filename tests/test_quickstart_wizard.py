"""Exercise the wizard against fake local commands, never a real Workshop or browser."""

import json
import signal
import socket
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

failure = os.environ.get("FAIL_STAGE", "")

if name == "uv":
    event("uv", args=args)
    if failure == "install":
        sys.exit(1)
    target = Path(os.environ["UV_PROJECT_ENVIRONMENT"]) / "bin"
    target.mkdir(parents=True)
    (target / "python").symlink_to(os.environ["FAKE_COMMAND"])
elif name == "python":
    if args[1] == "swift_read_only_mcp.key_management":
        event("server")
        if failure == "server-start":
            sys.exit(1)
        (state / "server-pid").write_text(str(os.getpid()))
        if failure != "server-timeout":
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
    if action == "validate" and (failure == "validate" or os.environ.get("INVALID_CONFIG")):
        print("SWIFT_MCP_CONTAINERS must be a JSON array of valid container names.",
              file=sys.stderr)
        sys.exit(1)
    if failure == action or (failure == "token-verify" and action == "token-status"
                             and (state / "token").exists()):
        sys.exit(1)
    if (action == "token-status" and os.environ.get("TOKEN_MISSING")
            and not (state / "token").exists()):
        sys.exit(3)
    if action == "store-token":
        if not sys.stdin.read().startswith("github_pat_"):
            sys.exit(1)
        (state / "token").write_text("stored")
    if action == "ensure-key":
        print("invalid" if failure == "invalid-key-id" else "42")
    if action == "caller-key":
        print("" if failure == "empty-key" else os.environ["FAKE_CALLER_SECRET"])
elif name == "workshop":
    if args == ["--version"]:
        event("workshop-version", has_swift=any(
            k.startswith(("OS_", "ST_", "SWIFT_MCP_", "SWIFTCLIENT_")) for k in os.environ
        ))
        if failure == "workshop-version":
            sys.exit(1)
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
        if failure == ("existing-list" if existing else "cleanup-list"):
            sys.exit(1)
        ready = (existing and os.environ.get("EXISTING")) or (project / "launched").exists()
        status = "Ready" if ready else "Off"
        print("swift-mcp-quickstart  " + status + "  -")
    elif command == "launch":
        (project / "launched").write_text("yes")
        if os.environ.get("LAUNCH_FAIL"):
            sys.exit(1)
        if failure == "server-after-launch":
            os.kill(int((state / "server-pid").read_text()), signal.SIGTERM)
            time.sleep(0.3)
    elif command == "connect" and failure == (
            "tunnel-connect" if args[3].endswith(":swift-mcp") else "token-connect"):
        sys.exit(1)
    elif command == "connect" and os.environ.get("CONNECT_FAIL") and "github-token" in args[3]:
        sys.exit(1)
    elif (command == "connect" and failure == "server-after-connect"
          and args[3].endswith(":github-token")):
        os.kill(int((state / "server-pid").read_text()), signal.SIGTERM)
        time.sleep(0.3)
    elif command == "disconnect" and failure == (
            "tunnel-disconnect" if args[3].endswith(":swift-mcp") else "token-disconnect"):
        sys.exit(1)
    elif command == "run":
        if failure == "copilot":
            sys.exit(7)
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
    if failure == "browser":
        sys.exit(1)
elif name in ("mktemp", "mkdir", "cp", "rm"):
    if failure == name:
        sys.exit(1)
    if name == "mktemp" and failure == "session-directory":
        print(str(state / "missing-directory"))
        sys.exit(0)
    os.execv("/usr/bin/" + name, [name, *args])
else:
    sys.exit(2)
"""


@pytest.fixture
def fake_environment(tmp_path):
    # Reserve a non-listening port for the fake server's prerequisite probe.
    # Other processes may use port 8000 while these simulated sessions run.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    probe_port = probe.getsockname()[1]
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    (checkout / "quickstart").symlink_to(ROOT / "quickstart", target_is_directory=True)
    wizard = scripts / WIZARD.name
    source = WIZARD.read_text()
    original_probe = "</dev/tcp/127.0.0.1/8000"
    assert source.count(original_probe) == 1
    wizard.write_text(source.replace(original_probe, f"</dev/tcp/127.0.0.1/{probe_port}"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "fake-command"
    fake.write_text(FAKE_COMMAND)
    fake.chmod(0o755)
    for name in ("uv", "workshop", "xdg-open", "mktemp", "mkdir", "cp", "rm"):
        (bin_dir / name).symlink_to(fake)
    sleep = bin_dir / "sleep"
    sleep.write_text('#!/bin/sh\n[ "$FAIL_STAGE" = server-timeout ] || exec /usr/bin/sleep "$@"\n')
    sleep.chmod(0o755)
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
        "CHECKOUT_PROJECT": str(checkout / "quickstart"),
        "FAKE_WIZARD": str(wizard),
        "SWIFT_MCP_OPENRC": "/protected/test.rc",
        "SWIFT_MCP_CONTAINERS": '["test"]',
        "OS_PASSWORD": "private-swift-password",
    }
    try:
        yield env, state, temporary
    finally:
        probe.close()


def run_wizard(env, input_text="\ny\n", wizard=None):
    return subprocess.run(
        ["bash", str(wizard or env["FAKE_WIZARD"])],
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
    assert all(event["project"] != env["CHECKOUT_PROJECT"] for event in workshop_events[1:])
    assert list(temporary.iterdir()) == []
    assert CALLER_SECRET not in result.stdout + result.stderr
    assert "private-swift-password" not in result.stdout + result.stderr


def test_temporary_session_defaults_to_yes(fake_environment):
    env, state, temporary = fake_environment
    result = run_wizard(env, "\n\n")
    assert result.returncode == 0, result.stderr
    assert "Continue with this temporary session? [Y/n]" in result.stdout
    assert "workshop-run" in [event["command"] for event in events(state)]
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize("answer", ["n\n", "no\n", "unexpected\n", ""])
def test_temporary_session_decline_or_eof_cancels(fake_environment, answer):
    env, state, temporary = fake_environment
    result = run_wizard(env, "\n" + answer)
    assert result.returncode != 0
    assert "Cancelled." in result.stderr
    assert "uv" not in [event["command"] for event in events(state)]
    assert list(temporary.iterdir()) == []


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
    assert "existing quickstart Workshop is present or its state is unknown" in result.stderr
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


@pytest.mark.parametrize(
    ("failure", "description"),
    [
        ("LAUNCH_FAIL", "Workshop launch failed"),
        ("CONNECT_FAIL", "Could not connect the Copilot token"),
        ("SERVER_EXITS", "Swift MCP stopped while Copilot was running"),
    ],
)
def test_partial_or_failed_sessions_are_cleaned(fake_environment, failure, description):
    env, state, temporary = fake_environment
    env[failure] = "1"
    result = run_wizard(env)
    assert result.returncode != 0
    assert description in result.stderr
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


@pytest.mark.parametrize(
    ("interrupt", "exit_status"),
    [(signal.SIGHUP, 129), (signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_interrupt_stops_the_client_and_removes_its_workshop(
    fake_environment, interrupt, exit_status
):
    env, state, temporary = fake_environment
    env["RUN_WAIT"] = "1"
    process = subprocess.Popen(
        ["bash", env["FAKE_WIZARD"]],
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
        process.send_signal(interrupt)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == exit_status, (stdout, stderr)
        assert f"interrupted by {interrupt.name}" in stderr
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
    assert "Swift MCP configuration validation failed; see the error above" in result.stderr
    assert "SWIFT_MCP_CONTAINERS must be a JSON array" in result.stderr
    commands = [event["command"] for event in events(state)]
    assert "helper-token-status" not in commands
    assert "workshop-launch" not in commands
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize(
    ("failure", "description"),
    [
        ("workshop-version", "Could not determine the Workshop version"),
        ("existing-list", "Could not inspect the existing quickstart Workshop"),
        ("mktemp", "Could not create a temporary session directory"),
        ("session-directory", "Could not access the temporary session directory"),
        ("install", "Swift MCP installation failed"),
        ("validate", "Swift MCP configuration validation failed; see the error above"),
        ("token-status", "Unlock the keyring and resolve any duplicate Copilot tokens"),
        ("ensure-key", "Inspect local caller-key state before retrying"),
        ("invalid-key-id", "Invalid caller key ID"),
        ("server-start", "Swift MCP exited during startup"),
        ("server-timeout", "Swift MCP did not become ready in time"),
        ("mkdir", "Could not prepare the temporary Workshop project"),
        ("cp", "Could not prepare the temporary Workshop project"),
        ("server-after-launch", "Swift MCP stopped during Workshop launch"),
        ("tunnel-connect", "Could not connect the tunnel"),
        ("token-connect", "Could not connect the Copilot token"),
        ("server-after-connect", "Swift MCP stopped before Copilot started"),
        ("caller-key", "Could not retrieve the caller key"),
        ("empty-key", "The caller key is empty"),
        ("copilot", "Copilot exited unsuccessfully"),
        ("token-disconnect", "Could not disconnect the Copilot token"),
        ("tunnel-disconnect", "Could not disconnect the tunnel"),
        ("cleanup-list", "Cleanup incomplete"),
        ("rm", "Could not remove temporary session"),
    ],
)
def test_failure_reports_the_affected_step(fake_environment, failure, description):
    env, state, temporary = fake_environment
    env["FAIL_STAGE"] = failure
    result = run_wizard(env)
    assert result.returncode != 0
    assert description in result.stderr, (result.stdout, result.stderr)
    if failure == "copilot":
        assert result.returncode == 7
    assert CALLER_SECRET not in result.stdout + result.stderr
    assert "private-swift-password" not in result.stdout + result.stderr
    if failure in {"cleanup-list", "rm"}:
        assert len(list(temporary.iterdir())) == 1
    else:
        assert list(temporary.iterdir()) == []


def test_cleanup_failure_does_not_hide_the_original_failure(fake_environment):
    env, state, temporary = fake_environment
    env.update(CONNECT_FAIL="1", REMOVE_FAIL="1")
    result = run_wizard(env)
    assert result.returncode != 0
    assert "Could not connect the Copilot token" in result.stderr
    assert "Cleanup incomplete" in result.stderr
    assert "workshop-remove" in [event["command"] for event in events(state)]
    assert len(list(temporary.iterdir())) == 1


def test_failed_browser_open_explains_the_manual_alternative(fake_environment):
    env, _, temporary = fake_environment
    env.update(TOKEN_MISSING="1", FAIL_STAGE="browser")
    result = run_wizard(env, "\ny\ngithub_pat_example\n")
    assert result.returncode == 0, result.stderr
    assert "couldn't open a browser, so visit it manually" in result.stdout
    assert "https://github.com/settings/personal-access-tokens/new?" in result.stdout
    assert "github_pat_example" not in result.stdout + result.stderr
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize("missing", ["SWIFT_MCP_OPENRC", "SWIFT_MCP_CONTAINERS"])
def test_empty_swift_setting_reports_both_are_required(fake_environment, missing):
    env, _, temporary = fake_environment
    del env[missing]
    result = run_wizard(env, "\ny\n\n")
    assert result.returncode != 0
    assert "Both Swift settings are required" in result.stderr
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize(
    ("failure", "description"),
    [
        ("store-token", "Could not store the Copilot token"),
        ("token-verify", "Could not verify the stored token"),
    ],
)
def test_token_setup_failures_explain_the_error_without_disclosing_token(
    fake_environment, failure, description
):
    env, _, temporary = fake_environment
    env.update(TOKEN_MISSING="1", FAIL_STAGE=failure)
    result = run_wizard(env, "\ny\ngithub_pat_example\n")
    assert result.returncode != 0
    assert description in result.stderr
    assert "github_pat_example" not in result.stdout + result.stderr
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize(
    ("missing", "input_text", "description"),
    [
        ("SWIFT_MCP_OPENRC", "\ny\n", "OpenStack RC file path was not provided"),
        ("SWIFT_MCP_CONTAINERS", "\ny\n", "Container allowlist was not provided"),
        ("TOKEN", "\ny\n", "Copilot token was not provided"),
    ],
)
def test_prompt_eof_explains_which_input_was_missing(
    fake_environment, missing, input_text, description
):
    env, _, temporary = fake_environment
    if missing == "TOKEN":
        env["TOKEN_MISSING"] = "1"
    else:
        del env[missing]
    result = run_wizard(env, input_text)
    assert result.returncode != 0
    assert description in result.stderr
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize("name", ["uv", "workshop"])
def test_missing_prerequisite_names_the_command(fake_environment, name):
    env, _, temporary = fake_environment
    (Path(env["PATH"].split(":")[0]) / name).unlink()
    result = run_wizard(env)
    assert result.returncode != 0
    assert f"Install {name} before starting" in result.stderr
    assert list(temporary.iterdir()) == []


def test_malformed_workshop_version_reports_why_it_cannot_continue(fake_environment):
    env, _, _ = fake_environment
    env["WORKSHOP_VERSION"] = "unknown"
    result = run_wizard(env)
    assert result.returncode != 0
    assert "Could not determine the Workshop version" in result.stderr


def test_wrong_port_reports_the_required_value(fake_environment):
    env, _, _ = fake_environment
    env["SWIFT_MCP_PORT"] = "8001"
    result = run_wizard(env)
    assert result.returncode != 0
    assert "requires SWIFT_MCP_PORT=8000" in result.stderr


def test_occupied_port_reports_the_conflict(fake_environment):
    env, _, _ = fake_environment
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", 8000))
        except OSError:
            pytest.skip("Port 8000 is already occupied")
        listener.listen()
        result = run_wizard(env, wizard=WIZARD)
    assert result.returncode != 0
    assert "Port 127.0.0.1:8000 is already in use" in result.stderr
