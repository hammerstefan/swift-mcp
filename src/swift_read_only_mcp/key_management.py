"""Local operator entry point for caller-key provisioning."""

import argparse
import fcntl
import itertools
import json
import os
import re
import secrets as random_secrets
import sqlite3
import stat
import sys
import time
from collections.abc import Callable
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Protocol

import secretstorage
from generateapikey import hash_api_key, verify_api_key
from generateapikey.integrations import APIKeyAuth, issue_api_key
from generateapikey.keystore import KeyStore


class SecretStore(Protocol):
    def get(self, attributes: dict[str, str]) -> str | None: ...

    def put(self, attributes: dict[str, str], value: str) -> None: ...


class Keyring:
    """Use the user's existing default Secret Service collection."""

    def get(self, attributes: dict[str, str]) -> str | None:
        with closing(secretstorage.dbus_init()) as connection:
            items = list(itertools.islice(secretstorage.search_items(connection, attributes), 2))
            if len(items) > 1:
                raise ValueError("Ambiguous secret")
            if not items:
                return None
            if items[0].is_locked():
                raise ValueError("Secret is locked")
            return items[0].get_secret().decode("utf-8", errors="strict")

    def put(self, attributes: dict[str, str], value: str) -> None:
        with closing(secretstorage.dbus_init()) as connection:
            if next(secretstorage.search_items(connection, attributes), None) is not None:
                raise ValueError("Secret already exists")
            collection = secretstorage.get_collection_by_alias(connection, "default")
            if collection.is_locked():
                raise ValueError("Secret collection is locked")
            collection.create_item(
                "Swift MCP credential", attributes, value.encode(), replace=False
            )
            items = list(itertools.islice(secretstorage.search_items(connection, attributes), 2))
            if len(items) != 1 or items[0].get_secret() != value.encode():
                raise ValueError("Secret could not be verified")


def _pepper_attributes() -> dict[str, str]:
    return {"service": "swift-mcp", "purpose": "pepper"}


def _caller_attributes(workshop: str, key_id: int) -> dict[str, str]:
    return {
        "service": "swift-mcp",
        "purpose": "caller",
        "workshop": workshop,
        "key-id": str(key_id),
    }


def _protected_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Unprotected state directory")


@contextmanager
def _management_lock(path: Path):
    _protected_directory(path)
    fd = os.open(path / "management.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Unprotected management lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _pending_key(
    database: Path, pepper: str, workshop: str, rotation_of: int | None = None
) -> tuple[int, str]:
    # KeyStore.create() commits an active key before its raw value can be stored.
    # Use the pinned library's issuer and schema to insert a disabled record first.
    issued = issue_api_key(pepper=pepper, prefix="smcp", min_entropy=128, expiry=1440)
    metadata = {"provisioning": True, "rotation_of": rotation_of}
    with sqlite3.connect(str(database)) as connection:
        cursor = connection.execute(
            """INSERT INTO api_keys
               (hashed, name, hint, scopes, created_at, expires_at, revoked_at, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                issued.hashed,
                workshop,
                issued.hint,
                "[]",
                int(time.time()),
                issued.key.expires_at,
                int(time.time()),
                json.dumps(metadata),
            ),
        )
        if cursor.lastrowid is None:
            raise ValueError("Could not create pending key")
        return cursor.lastrowid, str(issued.key)


def _activate_delivered_key(database: Path, key_id: int, rotation_of: int | None = None) -> None:
    pending = json.dumps({"provisioning": True, "rotation_of": rotation_of})
    metadata = json.dumps({"rotation_of": rotation_of})
    with sqlite3.connect(str(database)) as connection:
        result = connection.execute(
            """UPDATE api_keys SET revoked_at = NULL,
                      expires_at = CASE WHEN ? IS NULL THEN NULL ELSE expires_at END, meta = ?
               WHERE id = ? AND revoked_at IS NOT NULL AND expires_at IS NOT NULL
                 AND expires_at > ? AND meta = ?""",
            (rotation_of, metadata, key_id, int(time.time()), pending),
        )
        if result.rowcount != 1:
            raise ValueError("Delivered key is no longer active")


def _verify_pepper(database: Path, pepper: str) -> None:
    with sqlite3.connect(str(database)) as connection:
        row = connection.execute(
            "SELECT value FROM swift_mcp_metadata WHERE name = ?", ("pepper_check",)
        ).fetchone()
    if row is None or not verify_api_key("swift-mcp-pepper-check-v1", row[0], pepper):
        raise ValueError("Pepper does not match the key database")


def _initialize(workshop: str, secret_store: SecretStore, state_dir: Path) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", workshop):
        raise ValueError("Invalid Workshop name")
    _protected_directory(state_dir)
    database = state_dir / "keys.db"
    if secret_store.get(_pepper_attributes()) is not None:
        raise ValueError("Pepper already exists")
    fd = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    pepper = random_secrets.token_urlsafe(32)
    secret_store.put(_pepper_attributes(), pepper)
    if secret_store.get(_pepper_attributes()) != pepper:
        raise ValueError("Pepper delivery could not be verified")
    with sqlite3.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE swift_mcp_metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO swift_mcp_metadata (name, value) VALUES (?, ?)",
            ("pepper_check", hash_api_key("swift-mcp-pepper-check-v1", pepper)),
        )
    with KeyStore(str(database), pepper=pepper, touch=False):
        pass
    key_id, key = _pending_key(database, pepper, workshop)
    secret_store.put(_caller_attributes(workshop, key_id), key)
    if secret_store.get(_caller_attributes(workshop, key_id)) != key:
        raise ValueError("Key delivery could not be verified")
    _activate_delivered_key(database, key_id)
    print(json.dumps({"workshop": workshop, "key_id": key_id}))
    return 0


def _revoke(workshop: str, key_id: int, state_dir: Path) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", workshop) or key_id < 1:
        raise ValueError("Invalid key identifier")
    _protected_directory(state_dir)
    database = state_dir / "keys.db"
    info = database.lstat()
    if (
        database.is_symlink()
        or not database.is_file()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("Unprotected key database")
    with KeyStore(str(database), touch=False, table_check=False) as store:
        record = store.get(key_id)
        if record is None or record.name != workshop:
            raise ValueError("Key is not available")
        if record.revoked and record.meta.get("provisioning") is True:
            previous = record.meta.get("rotation_of")
            pending = json.dumps({"provisioning": True, "rotation_of": previous})
            cancelled = json.dumps({"rotation_of": previous})
            with sqlite3.connect(str(database)) as connection:
                result = connection.execute(
                    "UPDATE api_keys SET meta = ? "
                    "WHERE id = ? AND revoked_at IS NOT NULL AND meta = ?",
                    (cancelled, key_id, pending),
                )
                if result.rowcount != 1:
                    raise ValueError("Pending key is no longer available")
        elif not store.revoke(key_id):
            raise ValueError("Key is not available")
    print(json.dumps({"workshop": workshop, "key_id": key_id, "revoked": True}))
    return 0


def _add(workshop: str, secret_store: SecretStore, state_dir: Path) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", workshop):
        raise ValueError("Invalid Workshop name")
    _protected_directory(state_dir)
    database = state_dir / "keys.db"
    info = database.lstat()
    if (
        database.is_symlink()
        or not database.is_file()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("Unprotected key database")
    pepper = secret_store.get(_pepper_attributes())
    if not pepper:
        raise ValueError("Pepper is unavailable")
    _verify_pepper(database, pepper)
    with KeyStore(str(database), pepper=pepper, touch=False, table_check=False) as store:
        active = store.list(active_only=True)
        if any(record.name == workshop for record in active):
            raise ValueError("Workshop already has an active key")
        key_id, key = _pending_key(database, pepper, workshop)
        secret_store.put(_caller_attributes(workshop, key_id), key)
        if secret_store.get(_caller_attributes(workshop, key_id)) != key:
            raise ValueError("Key delivery could not be verified")
        _activate_delivered_key(database, key_id)
    print(json.dumps({"workshop": workshop, "key_id": key_id}))
    return 0


def _rotate(workshop: str, key_id: int, secret_store: SecretStore, state_dir: Path) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", workshop) or key_id < 1:
        raise ValueError("Invalid key identifier")
    _protected_directory(state_dir)
    database = state_dir / "keys.db"
    info = database.lstat()
    if (
        database.is_symlink()
        or not database.is_file()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("Unprotected key database")
    pepper = secret_store.get(_pepper_attributes())
    old_key = secret_store.get(_caller_attributes(workshop, key_id))
    if not pepper or not old_key:
        raise ValueError("Existing credentials are unavailable")
    with KeyStore(str(database), pepper=pepper, touch=False, table_check=False) as store:
        old = store.get(key_id)
        if old is None or old.name != workshop or not old.is_active:
            raise ValueError("Key is not active")
        if old.meta.get("rotation_of") is not None and old.expires_at is not None:
            raise ValueError("Replacement key has not been confirmed")
        verifier = APIKeyAuth(
            store.lookup, pepper=pepper, bearer=False, prefix="smcp-", require_checksum=True
        )
        if verifier.authenticate(old_key).id != key_id:
            raise ValueError("Existing key does not verify")
        if any(record.meta.get("rotation_of") == key_id for record in store.list(active_only=True)):
            raise ValueError("Rotation already exists")
        new_id, new_key = _pending_key(database, pepper, workshop, rotation_of=key_id)
        secret_store.put(_caller_attributes(workshop, new_id), new_key)
        if secret_store.get(_caller_attributes(workshop, new_id)) != new_key:
            raise ValueError("Key delivery could not be verified")
        _activate_delivered_key(database, new_id, rotation_of=key_id)
    print(json.dumps({"workshop": workshop, "key_id": new_id, "rotation_of": key_id}))
    return 0


def _confirm(
    workshop: str,
    key_id: int,
    secret_store: SecretStore,
    state_dir: Path,
    verify: Callable[[str], bool] | None,
) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", workshop) or key_id < 1:
        raise ValueError("Invalid key identifier")
    _protected_directory(state_dir)
    database = state_dir / "keys.db"
    info = database.lstat()
    if (
        database.is_symlink()
        or not database.is_file()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("Unprotected key database")
    pepper = secret_store.get(_pepper_attributes())
    new_key = secret_store.get(_caller_attributes(workshop, key_id))
    if not pepper or not new_key:
        raise ValueError("Replacement credentials are unavailable")
    with KeyStore(str(database), pepper=pepper, touch=False, table_check=False) as store:
        candidate = store.get(key_id)
        if candidate is None or candidate.name != workshop or not candidate.is_active:
            raise ValueError("Replacement key is not active")
        old_id = candidate.meta.get("rotation_of")
        if type(old_id) is not int or old_id < 1 or old_id == key_id:
            raise ValueError("Invalid rotation")
        verifier = APIKeyAuth(
            store.lookup, pepper=pepper, bearer=False, prefix="smcp-", require_checksum=True
        )
        if verifier.authenticate(new_key).id != key_id or (
            verify is not None and not verify(new_key)
        ):
            raise ValueError("Replacement key did not verify")
    verified_at = int(time.time())
    expiry = verified_at + 86_400
    with sqlite3.connect(str(database)) as connection:
        # Promote the replacement and shorten the old key in one SQLite transaction.
        connection.execute("BEGIN IMMEDIATE")
        now = int(time.time())
        if now >= expiry:
            raise ValueError("Verification is no longer current")
        result = connection.execute(
            """UPDATE api_keys SET expires_at = MIN(COALESCE(expires_at, ?), ?)
               WHERE id = ? AND name = ? AND revoked_at IS NULL
                 AND (expires_at IS NULL OR expires_at > ?)
                 AND EXISTS (SELECT 1 FROM api_keys WHERE id = ? AND name = ?
                             AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?))""",
            (expiry, expiry, old_id, workshop, now, key_id, workshop, now),
        )
        if result.rowcount != 1:
            raise ValueError("Rotation no longer active")
        result = connection.execute(
            """UPDATE api_keys SET expires_at = NULL
               WHERE id = ? AND name = ? AND revoked_at IS NULL
                 AND expires_at IS NOT NULL AND expires_at > ?""",
            (key_id, workshop, now),
        )
        if result.rowcount != 1:
            raise ValueError("Replacement key is no longer pending")
    print(json.dumps({"workshop": workshop, "key_id": key_id, "previous_key_id": old_id}))
    return 0


def _status(workshop: str, state_dir: Path) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", workshop):
        raise ValueError("Invalid Workshop name")
    database = state_dir / "keys.db"
    info = database.lstat()
    if (
        database.is_symlink()
        or not database.is_file()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("Unprotected key database")
    with KeyStore(str(database), touch=False, table_check=False) as store:
        records = [record for record in store.list() if record.name == workshop]
    print(
        json.dumps(
            {
                "workshop": workshop,
                "keys": [
                    {
                        "key_id": record.id,
                        "active": record.is_active,
                        "revoked": record.revoked,
                        "expires_at": record.expires_at,
                        "pending": record.meta.get("provisioning") is True,
                        "rotation_of": (
                            record.meta.get("rotation_of")
                            if type(record.meta.get("rotation_of")) is int
                            else None
                        ),
                    }
                    for record in records
                ],
            }
        )
    )
    return 0


def _recover(workshop: str, key_id: int, secret_store: SecretStore, state_dir: Path) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", workshop) or key_id < 1:
        raise ValueError("Invalid key identifier")
    database = state_dir / "keys.db"
    info = database.lstat()
    if (
        database.is_symlink()
        or not database.is_file()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("Unprotected key database")
    pepper = secret_store.get(_pepper_attributes())
    key = secret_store.get(_caller_attributes(workshop, key_id))
    if not pepper or not key:
        raise ValueError("Pending credentials are unavailable")
    _verify_pepper(database, pepper)
    with KeyStore(str(database), pepper=pepper, touch=False, table_check=False) as store:
        record = store.lookup(hash_api_key(key, pepper))
        if (
            record is None
            or record.id != key_id
            or record.name != workshop
            or not record.revoked
            or record.meta.get("provisioning") is not True
            or record.expires_at is None
            or record.expires_at <= time.time()
        ):
            raise ValueError("Pending key is not recoverable")
        previous = record.meta.get("rotation_of")
        if previous is not None:
            if type(previous) is not int or previous < 1:
                raise ValueError("Invalid pending rotation")
            old = store.get(previous)
            if old is None or old.name != workshop or not old.is_active:
                raise ValueError("Previous key is no longer active")
            if any(
                candidate.meta.get("rotation_of") == previous
                for candidate in store.list(active_only=True)
            ):
                raise ValueError("Another replacement is active")
        elif any(candidate.name == workshop for candidate in store.list(active_only=True)):
            raise ValueError("Workshop already has an active key")
        _activate_delivered_key(database, key_id, rotation_of=previous)
    print(json.dumps({"workshop": workshop, "key_id": key_id, "recovered": True}))
    return 0


def _startup_settings(workshop: str, secret_store: SecretStore, state_dir: Path) -> tuple[str, str]:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", workshop):
        raise ValueError("Invalid Workshop name")
    database = state_dir / "keys.db"
    info = database.lstat()
    if (
        database.is_symlink()
        or not database.is_file()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("Unprotected key database")
    pepper = secret_store.get(_pepper_attributes())
    if not pepper:
        raise ValueError("Pepper is unavailable")
    _verify_pepper(database, pepper)
    with KeyStore(str(database), pepper=pepper, touch=False, table_check=False) as store:
        verifier = APIKeyAuth(
            store.lookup, pepper=pepper, bearer=False, prefix="smcp-", require_checksum=True
        )
        for record in store.list(active_only=True):
            if record.name != workshop:
                continue
            key = secret_store.get(_caller_attributes(workshop, record.id))
            if not key:
                continue
            try:
                if verifier.authenticate(key).id == record.id:
                    return str(database), pepper
            except Exception:
                continue
    raise ValueError("No Workshop key verifies")


def main(
    argv: list[str] | None = None,
    *,
    secrets: SecretStore | None = None,
    state_dir: Path | None = None,
    verify: Callable[[str], bool] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Manage local Swift MCP caller keys")
    parser.add_argument(
        "action",
        choices=["init", "add", "revoke", "rotate", "confirm", "status", "start", "recover"],
    )
    parser.add_argument("workshop")
    parser.add_argument("key_id", nargs="?", type=int)
    parser.add_argument("--client-verified", action="store_true")
    args = parser.parse_args(argv)
    secret_store = secrets if secrets is not None else Keyring()
    directory = state_dir if state_dir is not None else Path.home() / ".local/state/swift-mcp"
    try:
        with _management_lock(directory):
            if args.action == "init" and args.key_id is None:
                return _initialize(args.workshop, secret_store, directory)
            if args.action == "add" and args.key_id is None:
                return _add(args.workshop, secret_store, directory)
            if args.action == "revoke" and args.key_id is not None:
                return _revoke(args.workshop, args.key_id, directory)
            if args.action == "rotate" and args.key_id is not None:
                return _rotate(args.workshop, args.key_id, secret_store, directory)
            if args.action == "confirm" and args.key_id is not None and args.client_verified:
                return _confirm(args.workshop, args.key_id, secret_store, directory, verify)
            if args.action == "status" and args.key_id is None:
                return _status(args.workshop, directory)
            if args.action == "recover" and args.key_id is not None:
                return _recover(args.workshop, args.key_id, secret_store, directory)
            if args.action == "start" and args.key_id is None:
                database, pepper = _startup_settings(args.workshop, secret_store, directory)
            else:
                raise ValueError("Invalid action arguments")
        os.environ["SWIFT_MCP_KEY_DB"] = database
        os.environ["SWIFT_MCP_KEY_PEPPER"] = pepper
        from swift_read_only_mcp.__main__ import main as start_server

        start_server()
        return 0
    except Exception:
        print("Key provisioning failed; inspect local state before retrying", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
