"""Private helpers for the interactive, host-local quickstart."""

import argparse
import contextlib
import io
import itertools
import json
import os
import re
import stat
import sys
from contextlib import closing
from pathlib import Path

import secretstorage

from swift_read_only_mcp.config import load_settings
from swift_read_only_mcp.key_management import Keyring, SecretStore
from swift_read_only_mcp.key_management import main as manage_keys
from swift_read_only_mcp.openrc import expand_openrc_path

WORKSHOP = "swift-mcp-quickstart"
TOKEN_ATTRIBUTES = {"service": "copilot-cli", "username": WORKSHOP}
VALIDATION_MESSAGES = {
    "Protect the RC file and keep it outside the checkout": (
        "The Swift RC file must be outside the checkout, owned by you, "
        "and have owner-only permissions (0600)."
    ),
    "Invalid Swift RC file": (
        "The Swift RC file is missing, unreadable, or not a valid regular file."
    ),
    "Invalid Swift RC assignment": (
        "The Swift RC file has an invalid or unsupported assignment; "
        "it is parsed as data, not executed as a shell script."
    ),
    "Invalid container allowlist": (
        "Invalid SWIFT_MCP_CONTAINERS: expected a JSON array of valid container names. "
        "In the wizard, enter names separated by commas or as a JSON array."
    ),
    "Swift authentication is not configured": (
        "The Swift RC file does not contain complete supported credentials."
    ),
    "Swift TLS verification must remain enabled": "Swift TLS verification must remain enabled.",
    "Invalid Swift endpoint URL": "The Swift RC file contains an invalid or insecure endpoint URL.",
    "Invalid Swift TLS configuration": "The Swift TLS certificate configuration is invalid.",
    "Invalid Swift client certificate settings": "OS_KEY requires an OS_CERT in the Swift RC file.",
    "Invalid page limit": "SWIFT_MCP_PAGE_LIMIT must be a positive integer.",
    "Invalid object byte limit": "SWIFT_MCP_BYTE_LIMIT must be a positive integer.",
    "Invalid listing byte limit": "SWIFT_MCP_LISTING_BYTE_LIMIT must be a positive integer.",
    "Invalid caller rate limit": "SWIFT_MCP_RATE_LIMIT must be a positive integer.",
}


def copilot_token_status() -> int:
    """Distinguish a missing token from an inaccessible or ambiguous keyring."""
    with closing(secretstorage.dbus_init()) as connection:
        collection = secretstorage.get_collection_by_alias(connection, "default")
        if collection.is_locked():
            raise ValueError("Secret collection is locked")
        matches = list(
            itertools.islice(secretstorage.search_items(connection, TOKEN_ATTRIBUTES), 2)
        )
        default = list(itertools.islice(collection.search_items(TOKEN_ATTRIBUTES), 2))
        if not matches and not default:
            return 3
        if (
            len(matches) != 1
            or len(default) != 1
            or matches[0].item_path != default[0].item_path
            or default[0].is_locked()
        ):
            raise ValueError("Copilot token is ambiguous or inaccessible")
        if not default[0].get_secret():
            raise ValueError("Copilot token is empty")
        return 0


def validate_config(root: Path) -> None:
    """Validate settings without opening a caller database or connecting to Swift."""
    try:
        path = Path(expand_openrc_path(os.environ["SWIFT_MCP_OPENRC"])).resolve(strict=True)
        info = path.stat()
    except OSError:
        raise ValueError("Invalid Swift RC file") from None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or path.is_relative_to(root.resolve())
    ):
        raise ValueError("Protect the RC file and keep it outside the checkout")
    if os.environ.get("SWIFTCLIENT_INSECURE", "false").lower() not in (
        "false",
        "0",
        "no",
        "off",
        "f",
        "n",
    ):
        raise ValueError("Swift TLS verification must remain enabled")
    env = dict(os.environ)
    # load_settings only checks that this path exists; no database is opened.
    env["SWIFT_MCP_KEY_DB"] = str(path)
    env["SWIFT_MCP_KEY_PEPPER"] = "quickstart-validation-only"
    if "SWIFT_MCP_CONTAINERS" not in env:
        raise ValueError("A container allowlist is required")
    load_settings(env)


def ensure_caller_key(secret_store: SecretStore, state_dir: Path) -> int:
    """Reuse one unambiguous active key, or provision one without replacing state."""

    def run(action: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = manage_keys([action, WORKSHOP], secrets=secret_store, state_dir=state_dir)
        if result:
            raise ValueError("Key management failed; inspect local state before retrying")
        return json.loads(output.getvalue())

    database = state_dir / "keys.db"
    if not os.path.lexists(database):
        key_id = run("init")["key_id"]
    else:
        records = run("status")["keys"]
        if any(record["pending"] for record in records):
            raise ValueError("A caller key is pending; inspect key management recovery")
        active = [record for record in records if record["active"]]
        if len(active) > 1:
            raise ValueError("Multiple active caller keys; finish rotation before retrying")
        key_id = active[0]["key_id"] if active else run("add")["key_id"]
    if type(key_id) is not int or key_id < 1:
        raise ValueError("Invalid caller key ID")
    if not secret_store.get(caller_attributes(key_id)):
        raise ValueError("The active caller key is unavailable in the keyring")
    return key_id


def caller_attributes(key_id: int) -> dict[str, str]:
    return {
        "service": "swift-mcp",
        "purpose": "caller",
        "workshop": WORKSHOP,
        "key-id": str(key_id),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Swift MCP quickstart helper")
    parser.add_argument(
        "action",
        choices=(
            "validate",
            "normalize-containers",
            "token-status",
            "store-token",
            "ensure-key",
            "caller-key",
        ),
    )
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    try:
        if args.action == "normalize-containers":
            raw = os.environ["SWIFT_MCP_CONTAINERS"]
            print(
                raw
                if raw.lstrip().startswith("[")
                else json.dumps([name.strip() for name in raw.split(",")])
            )
        elif args.action == "validate":
            if not args.value:
                raise ValueError("Missing checkout path")
            validate_config(Path(args.value))
        elif args.action == "token-status":
            return copilot_token_status()
        elif args.action == "store-token":
            token = sys.stdin.read(4097)
            if (
                not token.startswith("github_pat_")
                or not len("github_pat_") < len(token) <= 4096
                or any(char.isspace() or ord(char) < 32 for char in token)
            ):
                raise ValueError("Invalid Copilot token")
            Keyring().put(TOKEN_ATTRIBUTES, token)
        elif args.action == "ensure-key":
            print(ensure_caller_key(Keyring(), Path.home() / ".local/state/swift-mcp"))
        else:
            if not args.value or not re.fullmatch(r"[1-9][0-9]{0,18}", args.value):
                raise ValueError("Invalid caller key ID")
            key = Keyring().get(caller_attributes(int(args.value)))
            if not key:
                raise ValueError("Caller key unavailable")
            print(key)
        return 0
    except Exception as exc:
        message = None
        if args.action == "validate" and isinstance(exc, ValueError):
            message = VALIDATION_MESSAGES.get(str(exc))
        print(
            message or "Quickstart check failed; inspect configuration and local keyring state",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
