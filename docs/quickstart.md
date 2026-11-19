# Swift MCP and Workshop quickstart

Run Swift MCP on your Linux host and use Copilot CLI inside the included
`swift-mcp-quickstart` workshop to check the metadata of one Swift container.
You do not need Copilot CLI or a Copilot token on the host before you start.

## One-command quickstart

From the repository root, run:

```sh
just quickstart
```

The wizard prompts for a protected OpenStack RC file path and a JSON container
allowlist if `SWIFT_MCP_OPENRC` or `SWIFT_MCP_CONTAINERS` is unset or empty.
Alternatively, supply both in your host environment before starting:

```sh
export SWIFT_MCP_OPENRC='/path/to/test-swift.rc'
export SWIFT_MCP_CONTAINERS='["quickstart-test"]'
just quickstart
```

Use Linux, Bash 5.1 or later, Python 3.11 or later, `uv`, `just`, Workshop
0.9.8 or later, and an unlocked default Secret Service keyring. Run as the
unprivileged user who owns the keyring. Use a dedicated read-only Swift
identity. Keep its RC file outside the checkout, owned by that user and not
readable or writable by group or other users. Swift MCP reads the RC file as
data, not as a shell script; see [RC file constraints](../README.md#openstack-rc-file).
The wizard requires port `127.0.0.1:8000` and refuses to take over an existing
`swift-mcp-quickstart` Workshop in this checkout.

If the quickstart Copilot token is missing, the wizard opens the fine-grained
token form and asks you to paste the token at a hidden prompt. It stores the
token in your host keyring. It creates or reuses the separate MCP caller key
and selects its ID automatically. An existing token is checked for presence,
not validity or expiration; if Copilot rejects it, replace it in the keyring
after inspecting the old entry. Neither credential belongs in the RC file,
project files, shell history, or chat.

The wizard installs Swift MCP in a temporary environment, starts the server on
the host, and launches Copilot in a temporary container Workshop project. In
Copilot, open `/mcp`, then request `get_container_stats` for an allowed test
container. Check the actual tool result, not just the agent's summary.

On exit, interruption, or failure, the wizard disconnects its interfaces,
stops its server, and removes its Workshop and temporary project. **Files
created in that project are discarded.** The stored token, caller key, pepper,
and key database remain; shared `uv` and Workshop caches may also remain. A
power loss or `SIGKILL` cannot run cleanup. Use `workshop list --global` to
find a leftover project in a `swift-mcp-quickstart.*` temporary directory. If
removal fails, the wizard prints that directory: inspect its
`workshop -p <path>/project changes` and `tasks`, then remove that Workshop
when safe. Once the Workshop is removed, remove that temporary directory. Do
not remove unrelated Workshops or credentials.

## Manual two-terminal alternative

### Before you start

- Have a checkout of this repository, Python 3.11 or later, and
  [uv](https://docs.astral.sh/uv/).
- Install [`just`](https://just.systems/man/en/installation.html) on the host.
- Have a working [Workshop](https://ubuntu.com/workshop/docs/) installation
  (0.9.8 or later). Use the included container workshop, not a VM; Workshop
  VMs do not support tunnels.
- Use an unlocked default collection in your host user-session Secret Service
  keyring and install `secret-tool` (`libsecret-tools` on Ubuntu).
- Have a GitHub account with access to Copilot CLI.
- Have an OpenStack RC file for the Swift identity and the name of your container.
	- Using a [read-only Swift identity](../README.md#read-only-swift-deployment) is highly recommended.

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

## 2. Install Swift MCP

In your first host terminal, install the server:

```sh
uv sync --locked
```

The next step creates or reuses the Workshop's MCP caller key and prints its
numeric ID. The key itself stays in your host keyring. This key authenticates
Copilot to Swift MCP; the Copilot token authenticates Copilot to GitHub. If key
setup fails, see [key management](key-management.md) rather than deleting or
overwriting existing state.

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
the Workshop if needed, and connects the tunnel and Copilot token. It creates a
caller key if one does not exist and reuses an existing unambiguous active key;
it refuses incomplete or ambiguous key state. Note the printed caller key ID.
Leave this terminal open and continue when the recipe prompts you to run Copilot.

If launch or a connection fails, run `workshop -p quickstart changes` and then
`workshop -p quickstart tasks <change-id>` with the failed change's ID.
If setup was interrupted or cleanup failed, check
`workshop -p quickstart connections` and disconnect any remaining quickstart
connections before retrying.

## 4. Start copilot in workshop

In a second host terminal, replace `42` with the caller key ID printed in step 3:

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
