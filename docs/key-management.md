# Local caller-key management

Each Workshop uses its own **MCP caller key** to authenticate requests to the
host server. Revoking one Workshop's key does not revoke another's. All
authorized callers share the server's Swift identity and container allowlist.

To end access or respond to a suspected leak, [revoke the key](#revoke-a-caller-key).
To replace a working key without immediately cutting off its client, use
[routine rotation](#routine-rotation).

## Before you start

Run `uv run --locked swift-mcp-keys` commands from the repository root on the
Linux host, as the user who owns the keyring. Unlock the default collection in
your user-session Secret Service for provisioning and server startup. The CLI
uses Secret Service directly; the client delivery example also uses `secret-tool`.

| Item | Where it lives | What it is for |
| --- | --- | --- |
| Caller key (secret) | Host keyring; supplied to the Workshop client for a run | The client sends it to Swift MCP in `X-API-Key`. |
| Key ID (not secret) | Printed as `key_id` by the CLI | Selects the key to deliver, rotate, recover, or revoke. It cannot authenticate a request. |
| Pepper (secret) | Host keyring; loaded by the server at startup | Used to verify caller-key hashes in the host database. Never give it to the Workshop. |

### Host storage

The CLI creates `~/.local/state/swift-mcp/keys.db` with mode `0600` in a
user-owned directory with no group or other permissions. It locks management
operations; do not edit the database or keyring concurrently with another tool.
Keep secret values out of commands, terminal output, logs, project files,
issues, and shell history.

Before using GNOME Keyring for local storage, the operator must accept the
[storage exception in the specification](../.scratch/swift-read-only-mcp/spec.md#further-notes):
its AES-128-CBC encryption alone cannot detect changes to ciphertext. A store
that uses authenticated encryption is safer.

## Initial setup

Use the `name` in your Workshop definition. The examples below use
`swift-validation` for `validation/copilot/`. For `quickstart/`, the name is
`swift-mcp-quickstart` and the quickstart recipes handle provisioning. Names
must start with a lowercase letter, contain only lowercase letters, digits,
or hyphens, and be at most 40 characters long.

In **host terminal 1**, choose the command that matches your current state:

| Current state | Command |
| --- | --- |
| First Workshop for this host user; no database or pepper exists | `uv run --locked swift-mcp-keys init swift-validation` |
| Database exists; check this Workshop's IDs and status | `uv run --locked swift-mcp-keys status swift-validation` |
| Database exists; this Workshop has no active or pending key | `uv run --locked swift-mcp-keys add swift-validation` |

`init` creates the database, pepper, and first key; it refuses to replace an
existing database or pepper. `init` and `add` print the Workshop name and
numeric `key_id`, not the caller key. If `status` shows an active key, use its
ID. If setup failed partway through, see [recovery](#recover-an-interrupted-key-operation).

1. In terminal 1, [configure Swift credentials and the container allowlist](../README.md#3-configure-swift-and-start-the-server),
   then start the server once. `swift-mcp-keys start swift-validation` checks
   that the pepper matches the database and that a key for `swift-validation`
   verifies. Leave the terminal open. The running server retains the pepper
   in its environment.
2. In **host terminal 2**, also in the repository root, [connect the validation
   Workshop](../README.md#4-connect-the-validation-workshop) and [pass the caller
   key to Copilot](../README.md#pass-the-caller-key-to-copilot). Use the active
   key's ID to select the keyring item. Do not copy the key into a second
   item or run `secret-tool lookup` on its own: it prints the secret.

The exact lookup attributes for a managed caller key are `service=swift-mcp`,
`purpose=caller`, `workshop=swift-validation` (or your Workshop's name), and
`key-id` set to the numeric ID. They must match exactly one item. The pepper
has `service=swift-mcp` and `purpose=pepper`.

## Routine rotation

For `swift-mcp-quickstart`, start with the [two-terminal setup](quickstart.md#manual-two-terminal-alternative)
and leave the server running in terminal 1. The wizard and `quickstart-serve`
cannot choose a key while two are active.

1. **Create a replacement.** Replace `WORKSHOP_NAME` with your Workshop name
   and `OLD_ID` with the active key's numeric ID from `status`:

   ```sh
   uv run --locked swift-mcp-keys rotate WORKSHOP_NAME OLD_ID
   ```

   Note the **new** `key_id`. The old key is unchanged. The replacement
   expires in 24 hours unless you confirm it.
2. **Test the new key in the actual client.** Deliver the replacement using
   the new ID. Restart the client with that key (for the quickstart, replace
   `42` in `KEY_ID=42 just quickstart-copilot` in terminal 2). Make a Swift
   tool call, such as `get_container_stats` on an allowed test container. If
   the call fails, keep using the old key and do not confirm the replacement.
3. **Confirm the replacement.** Only after the call succeeds, replace `NEW_ID`
   below with the ID you tested:

   ```sh
   uv run --locked swift-mcp-keys confirm WORKSHOP_NAME NEW_ID --client-verified
   ```

   The flag records your confirmation. The command checks the key against the
   local database; it does not test the client. The new key remains active.
   The old key expires **at most 24 hours after confirmation**.

For the quickstart, wait for the old key to expire or revoke it after
confirmation before starting the wizard or `quickstart-serve` again.

## Revoke a caller key

For a suspected leak, revoke immediately rather than rotating. Use the same
procedure to retire a key after a one-time test. Revocation works even if the
keyring is locked.

1. Replace `WORKSHOP_NAME` with `swift-mcp-quickstart`, `swift-validation`, or
   your own Workshop's name. Find the affected ID or IDs:

   ```sh
   uv run --locked swift-mcp-keys status WORKSHOP_NAME
   ```

2. Replace `KEY_ID` with the numeric ID to revoke. Repeat for every compromised
   key, including any overlapping replacement:

   ```sh
   uv run --locked swift-mcp-keys revoke WORKSHOP_NAME KEY_ID
   ```

3. Run `status` again. The revoked key should show `revoked: true` and
   `active: false`. Swift MCP rejects further requests with it. Revocation
   does not erase the keyring item or remove a value already delivered to a
   process.

## Recover an interrupted key operation

New keys start disabled. The CLI activates a key only after verifying delivery
to the host keyring. Use `status` for the affected Workshop, then choose the
action that matches what you see:

- **Pending key, matching keyring item, key not expired:** Replace the name
  and ID in `uv run --locked swift-mcp-keys recover WORKSHOP_NAME KEY_ID`.
  The command checks the stored key before activating it.
- **Recovered rotation replacement:** Test the replacement in the Workshop
  client and [confirm it](#routine-rotation). Recovery does not confirm it.
- **Pending key you do not want to activate:** [Revoke its ID](#revoke-a-caller-key)
  to prevent recovery.
- **Initialization stopped before the database schema existed:** There is no
  automated recovery. Inspect the state directory and keyring before manual
  cleanup. Do not overwrite the database, pepper, or existing caller keys.

## Pepper rotation

**Pepper rotation is not supported. Do not replace the stored pepper by itself:**
existing caller keys will no longer verify. The proposed design requires an
offline maintenance window and reissuing every Workshop key. It is not an
available operator procedure.
