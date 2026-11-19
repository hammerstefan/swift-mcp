# Swift MCP and Workshop quickstart

Run Swift MCP on your Linux host and use Copilot CLI inside the included
`swift-validation` workshop to check the metadata of one Swift container.
You do not need Copilot CLI or a Copilot token on the host before you start.

**Use a controlled test Swift service and test data only.** Authentication
still needs independent security review, and Copilot interoperability is not
yet verified end to end. This quickstart is not for deployment.

## Before you start

- Have a checkout of this repository, Python 3.11 or later, and
  [uv](https://docs.astral.sh/uv/).
- Have a working [Workshop](https://ubuntu.com/workshop/docs/) installation
  (0.9.8 or later). Use the included container workshop, not a VM; Workshop
  VMs do not support tunnels.
- Use an unlocked default collection in your host user-session Secret Service
  keyring and install `secret-tool` (`libsecret-tools` on Ubuntu). Run the
  commands as the regular user who owns the keyring, not as root.
- Have a GitHub account with access to Copilot CLI, an OpenStack RC file for a
  read-only test Swift identity, and the name of an existing test container.

Run all shell commands from the repository root **on the host**. Workshop
mounts only `validation/copilot` at `/project`; keep your RC file outside the
entire checkout in a protected, owner-only location.

## 1. Create a Copilot token

In your browser, [create a fine-grained personal access token](https://github.com/settings/personal-access-tokens/new).
Select your personal account as **Resource owner**, choose **Public
repositories** (this guide needs no private repository access), and set a short
expiration. Under **Permissions → Account**, add **Copilot Requests**. Do not
add repository permissions. See [GitHub's authentication instructions](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli#authenticating-with-environment-variables).

Store the token in the host keyring. Paste it only at the `secret-tool` prompt:

```sh
secret-tool store --label="Swift MCP quickstart Copilot" --collection=default \
  service copilot-cli username swift-mcp-quickstart
```

In your local copy of [`validation/copilot/workshop.yaml`](../validation/copilot/workshop.yaml),
under `system` → `slots` → `copilot-token` → `attributes`, replace the existing
`username` value with `swift-mcp-quickstart`. Keep `service: copilot-cli`.
These are lookup labels, not credentials. The lookup must match exactly one
keyring item. Do not paste the token into YAML, a shell command, or Copilot chat.

## 2. Start Swift MCP on the host

In your first host terminal, install the server and create the Workshop's MCP
caller key:

```sh
uv sync --locked
uv run --locked swift-mcp-keys init swift-validation
```

Note the numeric `key_id` in the output. The command stores the key itself in
your host keyring. This key authenticates Copilot to Swift MCP; the Copilot
token authenticates Copilot to GitHub. Run `init` only once. If it reports
existing state, see [key management](key-management.md) rather than deleting or
overwriting anything.

Swift MCP parses the RC file; it does not source it. The required values must
be present as supported assignments. If the file needs to prompt for a
password or run shell commands to set any of those values, follow the
[host environment setup](../README.md#keystone-v3-password) instead.

Replace the example path with the absolute path to your RC file and
`quickstart-test` with the name of your test container:

```sh
export SWIFT_MCP_OPENRC='/path/to/test-swift.rc'
export SWIFT_MCP_CONTAINERS='["quickstart-test"]'
uv run --locked swift-mcp-keys start swift-validation
```

Leave this terminal open. The server listens on host loopback at
`http://127.0.0.1:8000/mcp`.

## 3. Launch and connect the workshop

In a second host terminal, from the repository root:

```sh
workshop -p validation/copilot launch
workshop -p validation/copilot info
```

Check for `status: ready`. Then connect the Copilot token and the Swift MCP
tunnel:

```sh
workshop -p validation/copilot connect swift-validation/copilot:github-token :copilot-token
workshop -p validation/copilot connect swift-validation/copilot:swift-mcp :swift-mcp
workshop -p validation/copilot connections swift-validation
```

Check that both connections show `manual`. If launch or a connection fails,
run `workshop -p validation/copilot changes` and then
`workshop -p validation/copilot tasks <change-id>` with the failed change's ID.

## 4. Verify one Swift tool call

Still in the second host terminal, replace `42` with the `key_id` printed in
step 2. This command retrieves the caller key without displaying it and passes
it to the Copilot action for this session only:

```sh
set +x
KEY_ID=42
COPILOT_MCP_SWIFT_KEY="$(secret-tool lookup service swift-mcp purpose caller \
  workshop swift-validation key-id "$KEY_ID")" \
  workshop -p validation/copilot run --env COPILOT_MCP_SWIFT_KEY swift-validation -- copilot
```

In Copilot, open `/mcp` and check for `swift-mcp`. Then enter the following
prompt with your test container name:

> Use the `swift-mcp` tool `get_container_stats` for `quickstart-test`.
> Show the object count and bytes used. Do not read any objects.

Approve only that tool call if prompted. Check the actual tool result for
`object_count` and `bytes_used`, not just Copilot's summary. With an allowlist,
`list_containers` only echoes allowed names, so it does not prove Swift is
reachable.

If Copilot asks you to sign in, check the `github-token` connection, keyring
lookup labels, token expiration, and **Copilot Requests** permission. If the
MCP server returns HTTP 401, check the caller key ID instead. Do not print
either secret while troubleshooting. See [more troubleshooting](../README.md#troubleshooting).

## Stop and disconnect

Exit Copilot and press `Ctrl+C` in the first terminal to stop Swift MCP. In the
second terminal, disconnect the token and tunnel, then stop the workshop:

```sh
workshop -p validation/copilot disconnect swift-validation/copilot:github-token
workshop -p validation/copilot disconnect swift-validation/copilot:swift-mcp
workshop -p validation/copilot stop
```

## Security notes

The tunnel is not authentication. Any process in the workshop can request the
connected Copilot token, and the MCP caller key is available to Copilot and
its child processes during the run. Keep approval prompts enabled and use
only test names and metadata you can share with Copilot's model provider.
Keep the RC file on the host and never add secrets to the checkout or logs.
If this was a one-time test, revoke the [GitHub token](https://github.com/settings/personal-access-tokens)
and [MCP caller key](key-management.md#routine-rotation) when finished.
