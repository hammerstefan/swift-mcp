# Local caller-key management

The host runs the server and holds the Swift identity. Each Workshop gets a
separate MCP caller key. The tunnel does not authenticate callers.

## Prerequisites

- Linux, a running user-session Secret Service, an unlocked default collection,
  and `secret-tool` to provide a caller key to a client. The management command
  uses the Secret Service API directly; its items are readable by `secret-tool`.
- Run as the unprivileged user who owns the host keyring. Never put a secret in
  a command argument, terminal output, log, project file, issue or shell history.
- The command creates `~/.local/state/swift-mcp/keys.db` with mode `0600` in a
  user-owned directory with no group or other permissions. It refuses to replace
  an existing database or pepper. Its lock serializes this command's operations;
  do not change the database or keyring concurrently through another tool.
- The operator has accepted the local keyring AES-128-CBC exception documented
  in the specification. CBC alone does not authenticate ciphertext; a store
  that uses authenticated encryption is the safer option.

## Initial setup

1. Unlock the host keyring. For a Workshop named `validation`, run:

   ```sh
   uv run --locked swift-mcp-keys init validation
   ```

   The command generates a pepper and key. It prints only the Workshop name and
   non-secret numeric key ID. The keyring items use these exact attributes:

   - Pepper: `service=swift-mcp`, `purpose=pepper`.
   - Caller key: `service=swift-mcp`, `purpose=caller`, `workshop=validation`,
     `key-id=<ID>`.

2. Provide the separately managed Swift credentials to the host process as
   described in the root README. Start the server with:

   ```sh
   uv run --locked swift-mcp-keys start validation
   ```

   It retrieves the pepper, checks that it matches the database, and checks
   that at least one key for the named Workshop is available and valid. It then
   starts the loopback server without printing secrets. The keyring must be
   unlocked at startup. The running process retains the pepper in its environment.

3. Give the Workshop its own key through its approved secret-delivery path.
   For the separate `validation/copilot` project, use the lookup attributes
   above when supplying its `COPILOT_MCP_SWIFT_KEY` environment variable; its
   README currently illustrates a different, manually stored keyring item.
   Verify there is exactly one matching keyring item. Never run a bare
   `secret-tool lookup`: it prints the secret.

Add another Workshop with `uv run --locked swift-mcp-keys add <workshop>`.
Workshop names must be lowercase letters, digits or hyphens, beginning with a
letter, and at most 40 characters. Inspect non-secret key IDs and status with
`uv run --locked swift-mcp-keys status <workshop>`.

## Routine rotation

1. Run `uv run --locked swift-mcp-keys rotate <workshop> <old-id>`. It creates a
   separate replacement and leaves the old key unchanged. The replacement
   expires after 24 hours if it is not confirmed.
2. Deliver the replacement using its new key ID and verify that the **actual
   Workshop client** can authenticate and use the server. Merely storing the
   key in the keyring is not client verification.
3. Only after that verification, run
   `uv run --locked swift-mcp-keys confirm <workshop> <new-id> --client-verified`.
   The flag is the operator's attestation; the command checks the replacement
   against the local verifier but does not independently test the Workshop
   client. The old key expires at most 24 hours after confirmation. The new key
   remains active. Existing client processes may need to be restarted to pick up
   the replacement.

For a suspected compromise, immediately run
`uv run --locked swift-mcp-keys revoke <workshop> <key-id>`. Revoke every
compromised key ID, including any overlapping replacement; `status` lists IDs.
Revocation works even if the keyring is locked. It does not erase the secret
from the keyring or recall a value already delivered to a process.

## Failures and pepper rotation

Keys are inserted disabled and become active only after delivery to the
keyring is verified. An interrupted delivery remains disabled. `status` marks
such a key as pending; if its matching keyring item exists and it has not
expired, `uv run --locked swift-mcp-keys recover <workshop> <key-id>` verifies and activates it. A recovered
rotation replacement still requires client verification and `confirm`. Revoke a
pending key to prevent recovery. If initialization fails before the database
schema exists, there is no automated recovery: inspect the keyring and state
directory before any manual cleanup; do not overwrite existing credentials.

**Pepper rotation is not implemented.** The chosen approach is an offline
maintenance window: stop the server, safely reissue every Workshop key with a
new pepper, update the protected database and keyring with recoverable cutover,
then restart and redeliver the keys. Do not change the stored pepper by itself:
existing key hashes will no longer match. Design, implementation and
interruption tests for that cutover remain open.

The isolated keyring integration test runs only when `dbus-run-session`,
`gnome-keyring-daemon` and `secret-tool` are installed; otherwise it is skipped.
It uses a temporary home and private D-Bus session, not the operator's keyring.
