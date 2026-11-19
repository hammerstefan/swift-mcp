# Swift MCP server

Swift MCP gives Model Context Protocol (MCP) clients read-only access to
OpenStack Swift: list containers and objects, inspect metadata, and read object contents. The
server runs on your host and keeps the Swift credentials there. A client in a
container Workshop connects through a local tunnel with its own caller key.

| Goal | Guide and project | Workshop name |
| --- | --- | --- |
| Make your first Swift call with Copilot and an OpenStack RC file | [Guided quickstart](docs/quickstart.md) in `quickstart/` | `swift-mcp-quickstart` |
| Set up the host server and connect Copilot yourself | [Manual setup below](#before-you-start) using `validation/copilot/` | `swift-validation` |

Copilot CLI connectivity through the validation Workshop has been verified.
To use another client, see [Connect an existing Workshop](#connect-an-existing-workshop).

## Before you start

You need:

- Linux, Python 3.11 or later, and [uv](https://docs.astral.sh/uv/).
- An unlocked user-session Secret Service keyring and `secret-tool`. On Ubuntu,
  `secret-tool` is provided by the `libsecret-tools` package. Run the server as
  the unprivileged user who owns the keyring, not as root.
- A [dedicated read-only Swift identity](#read-only-swift-deployment) with
  access to a test container whose name and metadata you can share with
  Copilot. Use either Keystone v3 password credentials or a pre-issued Swift
  token with its storage URL.
- [Workshop](https://ubuntu.com/workshop/docs/) 0.9.8 or later and a container
  Workshop. Workshop VM tunnels are not supported.

Run the manual setup from the repository root in **two host terminals** in the
same keyring session. Use terminal 1 for the server and terminal 2 for Copilot.

## 1. Install the server

```sh
uv sync --locked
```

## 2. Create or find a caller key

Unlock your keyring. In terminal 1, choose the command for this host user:

| Current state | Command |
| --- | --- |
| No Swift MCP key store yet | `uv run --locked swift-mcp-keys init swift-validation` |
| Store exists; `swift-validation` already has a key, or you're unsure | `uv run --locked swift-mcp-keys status swift-validation` |
| Store exists; `swift-validation` has no active or pending key | `uv run --locked swift-mcp-keys add swift-validation` |

Note the active key's numeric `key_id` for terminal 2. The ID is safe to
display. The client receives the **caller key**; the **pepper**, a secret used
to verify caller-key hashes, stays on the host. If setup failed partway through,
follow [key recovery](docs/key-management.md#recover-an-interrupted-key-operation)
before trying again.

## 3. Configure Swift and start the server

In terminal 1, choose where the server gets its Swift credentials:

- **RC file:** Follow [OpenStack RC file](#openstack-rc-file), then skip to
  [Restrict access and start](#restrict-access-and-start).
- **Environment:** Leave `SWIFT_MCP_OPENRC` unset. Follow either
  [Keystone v3 password](#keystone-v3-password) or [Pre-issued token](#pre-issued-token).

Keep secret values out of commands, shell history, project files, and logs.

### OpenStack RC file

Use a protected RC file from your secret manager, owned by your user and
readable only by you, outside the checkout (preferably on a memory-backed
filesystem). Keep it on the host. The [example RC file](validation/example_creds.rc)
shows the format using placeholders. Set the path to **your** file:

```sh
export SWIFT_MCP_OPENRC="$HOME/credentials.rc"
```

The file must supply one complete credential mode: password or token. The
server reads it once at startup without running shell commands. If it needs
to prompt for a password or run a command, use the environment setup instead.
See [RC file syntax and precedence](#rc-file-syntax-and-precedence) for details.

### Keystone v3 password

Set these variables in the host terminal, replacing the example values with
those for your test account:

```sh
export OS_AUTH_URL="https://identity.example.com/v3"
export ST_AUTH_VERSION=3
export OS_USERNAME="your-username"
export OS_USER_DOMAIN_NAME="your-user-domain"
export OS_PROJECT_NAME="your-project"
export OS_PROJECT_DOMAIN_NAME="your-project-domain"
```

To provide `OS_PASSWORD` from the host keyring, store the test password once.
Enter it at the prompt:

```sh
secret-tool store --label="Swift test password" --collection=default service swift-mcp purpose swift-password
```

Retrieve it in the server terminal with shell tracing off:

```sh
set +x
OS_PASSWORD="$(secret-tool lookup service swift-mcp purpose swift-password)"
export OS_PASSWORD
```

Alternatively, use `OS_USER_ID` instead of the user name and domain, and
`OS_PROJECT_ID` instead of the project name and domain. You can set
`OS_REGION_NAME`, `OS_SERVICE_TYPE`, and `OS_INTERFACE` to select a Swift
endpoint from Keystone's service catalog. `OS_ENDPOINT_TYPE` is also supported;
if both it and `OS_INTERFACE` are set, `OS_ENDPOINT_TYPE` takes precedence.

If you set `OS_STORAGE_URL`, it overrides catalog selection. Check that the URL
belongs to the intended Swift account: the server sends its Keystone token
there. Leave `OS_AUTH_TOKEN` unset in password mode.

### Pre-issued token

Set `OS_STORAGE_URL` to your test Swift storage URL:

```sh
export OS_STORAGE_URL="https://storage.example.com/v1/your-account"
```

Provide `OS_AUTH_TOKEN` securely from your secret manager. For example, enter
the token at the prompt, then retrieve it without displaying it:

```sh
secret-tool store --label="Swift test token" --collection=default service swift-mcp purpose swift-token
set +x
OS_AUTH_TOKEN="$(secret-tool lookup service swift-mcp purpose swift-token)"
export OS_AUTH_TOKEN
```

Do not also set the Keystone password-mode variables above: the server rejects
mixed credential modes. The server does not refresh pre-issued tokens; replace
an expired token and restart the server.

### Restrict access and start

Replace `test-container` with the name of your test container. Set the
allowlist in terminal 1 **before** starting the server:

```sh
export SWIFT_MCP_CONTAINERS='["test-container"]'
```

If this variable is unset, callers can access every container the Swift
identity can read. Set it to `[]` to allow none. All callers share the same
Swift identity and allowlist. Container names must not contain `/` or be `.` or
`..`. Object names can contain `/`, but cannot contain `.` or `..` as complete
segments.

External Swift and Keystone endpoints must use HTTPS. Only loopback test
endpoints can use plain HTTP. If your service uses a private certificate
authority, set `OS_CACERT` to its trusted CA file; keep TLS verification enabled.

Start the server in the foreground:

```sh
uv run --locked swift-mcp-keys start swift-validation
```

Leave this terminal open. The command retrieves the pepper from the keyring,
checks that the Workshop has a valid key, and starts the server at
`http://127.0.0.1:8000/mcp`. Stop it with `Ctrl+C`.

## 4. Connect the validation Workshop

In **terminal 2**, open the repository root. The GitHub token authenticates
Copilot to GitHub. The MCP caller key authenticates Copilot to Swift MCP.

1. In [the validation Workshop definition](validation/copilot/workshop.yaml),
   set the `copilot-token` lookup attributes to match exactly one
   [supported Copilot token](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli#authenticating-with-environment-variables)
   in your host keyring. The checked-in `username` is account-specific. If you
   need to store a token, choose a unique label and use it for `username` in
   the YAML and in place of `your-account-label` below. Enter the token only
   at the prompt:

   ```sh
   secret-tool store --label="Copilot token" --collection=default service copilot-cli username your-account-label
   ```

2. On first use, launch the Workshop:

   ```sh
   workshop -p validation/copilot launch
   ```

   On later runs, use `workshop -p validation/copilot start` if it is stopped.
   If you changed its definition, run `workshop -p validation/copilot refresh`
   while it is running.

3. Connect the token and tunnel. If either is already connected to the expected
   slot, skip its `connect` command:

   ```sh
   workshop -p validation/copilot connect swift-validation/copilot:github-token :copilot-token
   workshop -p validation/copilot connect swift-validation/copilot:swift-mcp :swift-mcp
   workshop -p validation/copilot connections swift-validation
   ```

   Check that both connections appear as `manual`. The tunnel connects the
   Workshop's `127.0.0.1:8000` to the host server; it does not authenticate
   callers.

### Pass the caller key to Copilot

Replace `42` with the active `key_id` from [caller-key setup](#2-create-or-find-a-caller-key).
Turn off shell tracing before retrieving the key. `--env` takes the variable's **name**:

```sh
set +x
KEY_ID=42
COPILOT_MCP_SWIFT_KEY="$(secret-tool lookup service swift-mcp purpose caller workshop swift-validation key-id "$KEY_ID")" \
  workshop -p validation/copilot run --env COPILOT_MCP_SWIFT_KEY swift-validation -- copilot
```

The action loads [the MCP configuration](validation/copilot/mcp-config.json).
Copilot sends the key in `X-API-Key`; the action refuses to start without it.
Open `/mcp` and check for `swift-mcp`. Ask Copilot to call `get_container_stats`
for the container you allowed. Inspect the **tool result** for `object_count`
and `bytes_used`. With an allowlist, `list_containers` can succeed without
contacting Swift.

When finished, exit Copilot. In terminal 2, disconnect both interfaces and
stop the Workshop. Then press `Ctrl+C` in terminal 1 to stop the server.

```sh
workshop -p validation/copilot disconnect swift-validation/copilot:github-token
workshop -p validation/copilot disconnect swift-validation/copilot:swift-mcp
workshop -p validation/copilot stop
```

If you change `SWIFT_MCP_PORT` on the host, update both tunnel endpoints in
`validation/copilot/workshop.yaml` and the URL in
`validation/copilot/mcp-config.json` to the same port. Refresh the Workshop and
reconnect the tunnel after changing its endpoints. After `workshop restore` or
removal and relaunch, [connect both the token and the tunnel](#4-connect-the-validation-workshop)
and check that both show `manual`.

## Connect an existing Workshop

Create a [caller key](docs/key-management.md#initial-setup) using the name in
your Workshop definition. The `system` SDK provides the **slot** (host-side
endpoint); the client SDK uses the **plug** (Workshop-side endpoint). Merge
these entries into your definition. Replace `copilot` with your client SDK:

```yaml
sdks:
  - name: system
    slots:
      swift-mcp:
        interface: tunnel
        endpoint: 127.0.0.1:8000
  - name: copilot
    plugs:
      swift-mcp:
        interface: tunnel
        endpoint: 127.0.0.1:8000
```

From that Workshop's project directory, launch it or refresh the running
Workshop. Connect the tunnel explicitly, replacing `my-workshop` and
`copilot` with your Workshop and client SDK names:

```sh
workshop connect my-workshop/copilot:swift-mcp :swift-mcp
workshop connections my-workshop
```

Configure the client for Streamable HTTP at `http://127.0.0.1:8000/mcp` and
send the Workshop's caller key in the `X-API-Key` header on every request.
Pass the key from the host keyring for a single command using `--env`, as in
the [Copilot example](#pass-the-caller-key-to-copilot). Keep the value out of
the Workshop definition and MCP configuration file. Verify the setup with
`get_container_stats` on an allowed container. See the [Workshop secrets guide](https://ubuntu.com/workshop/docs/how-to/develop-with-workshops/provide-secrets.md)
and [OpenCode MCP configuration guide](https://opencode.ai/docs/mcp-servers) for client setup.

## Available tools and limits

| Scope | Tools |
| --- | --- |
| Account | `get_account_stats`, `list_containers` |
| Container | `get_container_stats`, `list_objects` |
| Current object | `get_object_stats` for metadata; `read_object` for a byte range |

Use the resource `swift://object/{container}/{object_name}` to read a complete
object that fits within the byte limit. Percent-encode special characters in
the URI. For larger objects, use `read_object` with `offset` and `length`.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `SWIFT_MCP_PORT` | `8000` | Host loopback port |
| `SWIFT_MCP_BYTE_LIMIT` | `1048576` | Maximum bytes per object read |
| `SWIFT_MCP_PAGE_LIMIT` | `1000` | Maximum entries per listing page |
| `SWIFT_MCP_LISTING_BYTE_LIMIT` | `4194304` | Maximum backend listing response bytes |
| `SWIFT_MCP_RATE_LIMIT` | `60` | Requests per caller key per minute |
| `SWIFT_MCP_OPENRC` | unset | Path to an OpenStack RC file supplying Swift authentication |
| `SWIFT_MCP_CONTAINERS` | unset (all readable containers) | [JSON array of allowed container names](#restrict-access-and-start); `[]` allows none |

The numeric limits must be positive integers; the port must also be at most 65535.
Large limits can use substantial memory.

With a container allowlist, `list_containers` returns the configured names, not
an account-wide listing, and `get_account_stats` is unavailable. An allowlisted
name can appear even if the container is missing or unreadable; use
`get_container_stats` to check it. See [ADR 0002](docs/adr/0002-allowlisted-container-listing.md).

## RC file syntax and precedence

`SWIFT_MCP_OPENRC` must point to a UTF-8 regular file no larger than 64 KiB.
A leading `~/` resolves to the server user's home directory.

- **Accepted assignments:** `NAME=value` and `export NAME=value` for the Swift
  authentication and endpoint variables above, including `OS_USER_DOMAIN_ID`,
  `OS_PROJECT_DOMAIN_ID`, `OS_TENANT_NAME`, `OS_AUTH_VERSION`, `OS_IDENTITY_API_VERSION`,
  `ST_AUTH_VERSION`, `OS_CACERT`, `OS_CERT`, and `OS_KEY`. Values can use single
  or double quotes. `$NAME` and `${NAME}` can refer to an earlier supported
  assignment outside single quotes.
- **Rejected assignments:** Unknown `OS_*` variables, duplicates, undefined
  references, command substitutions, and unsupported shell syntax in a Swift
  assignment cause startup to fail.
- **Ignored lines:** The server does not execute the file. It ignores other
  lines, including `NOVA_*` assignments and shell commands; do not rely on
  prompts or conditionals to set credentials.
- **Precedence:** Swift credentials come entirely from the file. Swift
  authentication variables already set in the host environment are ignored.
  TLS settings can come from the environment, but values in the file take
  precedence. Restart the server after changing the file.

## Troubleshooting

- **Server does not start:** Check that you supplied one complete Swift
  credential mode and unlocked the host keyring. To check for an active
  caller key, run `uv run --locked swift-mcp-keys status swift-validation`.
- **Client cannot reach the server:** Check that the host process is running,
  both tunnel endpoints and the client URL use the same port, and the tunnel is
  connected with `workshop -p validation/copilot connections swift-validation`.
- **HTTP 401:** Check that the client sends the correct, active Workshop key in
  `X-API-Key`. Do not print the key while troubleshooting.
- **HTTP 503, `Server busy`:** Eight requests are already active. Reduce the
  number of parallel calls and retry with backoff.
- **`Exceeded concurrency limit` in the server log:** Uvicorn reached its
  separate limit of 32 open connections or tasks. Idle keep-alive connections
  count toward this limit; close unused connections before retrying.
- **Workshop launch or connection fails:** Inspect
  `workshop -p validation/copilot changes` and then
  `workshop -p validation/copilot tasks <change-id>`.

## Security notes

The server listens only on host loopback. The tunnel provides connectivity;
the caller key authenticates each request. Any process in the Workshop can
request the connected Copilot token. Copilot and its child processes can also
read the MCP caller key during a run. Disconnect unused interfaces and check
the agent's permissions. Swift object contents are untrusted and may be sent
to the agent's model provider.

Protect the host keyring, key database, and logs. See [key management](docs/key-management.md)
for the keyring storage limitation, rotation, and immediate revocation.

### Read-only Swift deployment

The server sends only `GET` and `HEAD` requests to Swift. In password mode it
also sends a `POST` to Keystone to obtain a token. The server rejects redirects
on both paths. Use a pre-issued token if it must not make the Keystone `POST`.

Before deploying, have your Swift administrator provision a dedicated identity
whose effective policy and ACLs deny all account, container, and object
writes, including metadata and ACL changes. The server validates credential
configuration, not effective Swift permissions. Do not give it an account
owner, operator, reseller administrator, or other write-capable credential.
Keep certificate verification enabled and treat the configured endpoints or
the Keystone catalog as trusted configuration.

For Swift with Keystone auth, one option is to grant the dedicated identity
access through `X-Container-Read` only, using the `project-id:user-id` form.
Do not grant it `X-Container-Write` access, including through a group or role.
Point `OS_STORAGE_URL` at the account that owns those containers and set
`SWIFT_MCP_CONTAINERS` to the intended names. This avoids account-wide calls,
which container-level ACLs do not grant. See the [Swift ACL documentation](https://docs.openstack.org/swift/latest/overview_acl.html).
Review the effective policy before enabling MCP access and after any change to
roles or ACLs. If you also use a read-only gateway, permit only `GET` and `HEAD`
for the intended Swift account, and prevent the MCP host from bypassing it.

## Development checks

```sh
uv sync --locked --dev
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check src tests
```

The tests use controlled local Swift and Keystone endpoints; they do not need
live storage credentials. See [conformance testing](docs/conformance.md) for
the separate MCP fixture and its results. These automated checks do not run
against a live Swift service or through a Workshop.

The keyring integration test uses a temporary home and private D-Bus session,
not your keyring. It skips if `dbus-run-session`, `gnome-keyring-daemon`, or
`secret-tool` is missing.

The pytest suite includes Hypothesis property tests. To run just those tests:

```sh
uv run --locked pytest -q tests/test_properties.py
```

Hypothesis prints a minimal failing example when it finds a counterexample and
stores examples locally in `.hypothesis/`. To reproduce a run with a known seed,
add `--hypothesis-seed=<seed>` to the pytest command. Add a regression test or
an explicit example for any important failure; the local example database is
not shared with CI.

## License

This project is licensed under the GNU General Public License v3.0 only
(SPDX: `GPL-3.0-only`). See [LICENSE](LICENSE) for the full license text.
