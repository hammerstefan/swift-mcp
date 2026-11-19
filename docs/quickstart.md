# Swift MCP and Workshop quickstart

Run Swift MCP on your Linux host and use Copilot CLI inside the included
`swift-mcp-quickstart` workshop to check the metadata of one Swift container.
You do not need Copilot CLI or a Copilot token on the host before you start.

## Before you start

- Have a checkout of this repository, Python 3.11 or later, and
  [uv](https://docs.astral.sh/uv/).
- Install [`just`](https://just.systems/man/en/installation.html) on the host.
- Have a working [Workshop](https://ubuntu.com/workshop/docs/) installation
  (0.9.8 or later). Use the included container workshop, not a VM; Workshop
  VMs do not support tunnels.
- Use an unlocked default collection in your host user-session Secret Service
  keyring and install `secret-tool` (`libsecret-tools` on Ubuntu).
- Have a GitHub account with access to Copilot CLI.
- Have an OpenStack RC file for a Swift identity and the name of an existing
  test container.

Run all shell commands from the repository root **on the host**. Workshop
mounts only `quickstart/` at `/project`; keep your RC file outside the
entire checkout in a protected, owner-only location.

## 1. Create a Copilot token

Open the [prefilled fine-grained token form](https://github.com/settings/personal-access-tokens/new?name=Swift%20MCP%20quickstart&expires_in=7&user_copilot_requests=read).
Before generating the token, check that it expires in seven days, the
**Resource owner** is your personal account, and **Repository access** is
**Public repositories**. Under **Permissions → Account**, select
**Copilot Requests** with **Read** access if it isn't already selected. Do not
add other permissions. See [GitHub's authentication instructions](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli#authenticating-with-environment-variables).

Store the token in the host keyring. Paste it only at the `secret-tool` prompt:

```sh
secret-tool store --label="Swift MCP quickstart Copilot" --collection=default \
  service copilot-cli username swift-mcp-quickstart
```

The [quickstart Workshop definition](../quickstart/workshop.yaml) already uses
these lookup labels. You do not need to edit it. The lookup must match exactly
one keyring item. Do not paste the token into YAML, a shell command, or Copilot
chat.

## 2. Install Swift MCP and create a caller key

In your first host terminal, install the server and create the Workshop's MCP
caller key:

```sh
uv sync --locked
uv run --locked swift-mcp-keys init swift-mcp-quickstart
```

Note the numeric `key_id` in the output. The command stores the key itself in
your host keyring. This key authenticates Copilot to Swift MCP; the Copilot
token authenticates Copilot to GitHub. Run `init` only once. If it reports
existing state, see [key management](key-management.md) rather than deleting or
overwriting anything.

Swift MCP parses the RC file; it does not source it. The required values must
be present as supported assignments. If the file needs to prompt for a
password or run shell commands to set any of those values, follow the
[host environment setup](../README.md#keystone-v3-password) in the first
terminal and omit `SWIFT_MCP_OPENRC` below.

## 3. Start the server and connect the Workshop

In the first terminal, replace the example RC path and container name:

```sh
export SWIFT_MCP_OPENRC='/path/to/test-swift.rc'
export SWIFT_MCP_CONTAINERS='["quickstart-test"]'
just quickstart-serve
```

`just quickstart-serve` starts Swift MCP on `127.0.0.1:8000`, launches or starts
the Workshop if needed, and connects the tunnel and Copilot token. Leave this
terminal open and continue when the recipe prompts you to run Copilot.

If launch or a connection fails, run `workshop -p quickstart changes` and then
`workshop -p quickstart tasks <change-id>` with the failed change's ID.
If setup was interrupted or cleanup failed, check
`workshop -p quickstart connections` and disconnect any remaining quickstart
connections before retrying.

## 4. Start copilot in workshop

In a second host terminal, replace `42` with the `key_id` printed in step 2:

```sh
KEY_ID=42 just quickstart-copilot
```

The recipe retrieves the caller key from the host keyring without displaying it
and passes it to Copilot for this run only.

## 5. Verify one Swift tool call

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

Exit Copilot and press `Ctrl+C` in the first terminal. The recipe stops Swift
MCP and disconnects the token and tunnel. In the second terminal, stop the
Workshop:

```sh
workshop -p quickstart stop
```

## Security notes

The tunnel is not authentication. Any process in the workshop can request the
connected Copilot token, and the MCP caller key is available to Copilot and
its child processes during the run. Keep approval prompts enabled and use
only test names and metadata you can share with Copilot's model provider.
Keep the RC file on the host and never add secrets to the checkout or logs.
If this was a one-time test, revoke the [GitHub token](https://github.com/settings/personal-access-tokens)
and [MCP caller key](key-management.md#routine-rotation) when finished.
