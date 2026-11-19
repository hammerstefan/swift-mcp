# Swift MCP server

This server gives an MCP client read-only access to OpenStack Swift. It runs on
your host, keeps the Swift credentials there, and lets a container Workshop
connect through a local tunnel. Give each Workshop its own MCP caller key.

Copilot CLI connectivity to Swift MCP through the included container Workshop
has been verified. The examples below use a controlled Swift service and test data.

**First time with an OpenStack RC file and Copilot?** Follow the
[Swift MCP and Workshop quickstart](docs/quickstart.md).

## Before you start

You need:

- Linux, Python 3.11 or later, and [uv](https://docs.astral.sh/uv/).
- An unlocked user-session Secret Service keyring and `secret-tool`. On Ubuntu,
  `secret-tool` is provided by the `libsecret-tools` package. Run the server as
  the unprivileged user who owns the keyring, not as root.
- A test Swift account and either a Keystone v3 password or a pre-issued Swift
  token and storage URL. Use a Swift identity with read-only permissions.
- [Workshop](https://ubuntu.com/workshop/docs/) 0.9.8 or later and a container
  Workshop. Workshop VM tunnels are not supported.

The examples below run from the root of this repository, on the host. They use
the included Copilot validation Workshop, named `swift-validation`.

## 1. Install the server

```sh
uv sync --locked
```

## 2. Create a caller key

Unlock your keyring, then run:

```sh
uv run --locked swift-mcp-keys init swift-validation
```

This creates a protected key database at `~/.local/state/swift-mcp/keys.db` and
stores a pepper and a new caller key in your keyring. It prints a non-secret
numeric `key_id`; note it down for the Workshop command below. It does **not**
print the key itself.

Run `init` only for the first Workshop on this host. If you have already
initialized the key database, create a key for another Workshop instead:

```sh
uv run --locked swift-mcp-keys add swift-validation
```

If `swift-validation` already has an active key, use
`uv run --locked swift-mcp-keys status swift-validation` to find its ID.

If initialization fails, inspect the local state before retrying; do not
overwrite the database or keyring items. See [key management](docs/key-management.md)
for recovery, rotation, and revocation. Pepper rotation is not implemented.

## 3. Configure Swift and start the server

Choose **one** of the following credential modes. Provide secrets from your
host's secret manager; do not put secret values in project files, commands, shell
history, or logs. Turn off shell tracing (`set +x`) before retrieving secrets.

### OpenStack RC file

You can provide an OpenStack RC file like `validation/example_creds.rc` instead
of exporting the Swift authentication variables individually:

```sh
export SWIFT_MCP_OPENRC=/path/to/credentials.rc
uv run --locked swift-mcp-keys start swift-validation
```

The file is read once at startup and must be a UTF-8 regular file no larger than
64 KiB. It can contain `export` or plain assignments for the Swift `OS_*`
authentication and endpoint variables described below, `OS_TENANT_NAME`,
`ST_AUTH_VERSION`, `OS_CACERT`, `OS_CERT`, and `OS_KEY`. Values can use single
or double quotes and `$NAME` or `${NAME}` references to supported assignments
earlier in the same file. Unsupported `OS_*` assignments, duplicates, undefined
references, shell expansions and command substitutions in those assignments
cause startup to fail. The file is **not sourced**: other lines, including
`NOVA_*` and shell commands, are ignored. Do not rely on conditionals, prompts,
or other shell processing to set credentials. Use the environment-based setup
below if the RC file needs such processing.

When `SWIFT_MCP_OPENRC` is set, the file supplies the complete Swift
authentication configuration; ambient Swift authentication variables are
ignored. TLS settings may still come from the environment, with file values
taking precedence. The same credential-mode and endpoint validation applies.
Restart the server after changing the file.

An RC file containing a password or token is a secret. Prefer the environment
and host keyring setup below; if an RC file is required, obtain it securely from
your secret manager into a protected, owner-only location outside the repository
(preferably a memory-backed filesystem). Do not commit it, copy it into a
Workshop, or expose it in logs. The example contains placeholders and is not a
usable credential. Use only a read-only test identity.

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

Set `OS_PASSWORD` securely in the same terminal. For example, to store the test
password in the host keyring, run the following command and enter it at the
prompt:

```sh
secret-tool store --label="Swift test password" --collection=default service swift-mcp purpose swift-password
```

Then retrieve it without displaying it:

```sh
OS_PASSWORD="$(secret-tool lookup service swift-mcp purpose swift-password)"
export OS_PASSWORD
```

Alternatively, use `OS_USER_ID` instead of the user name and domain, and
`OS_PROJECT_ID` instead of the project name and domain. You can set
`OS_REGION_NAME`, `OS_SERVICE_TYPE`, and `OS_INTERFACE` to select a Swift
endpoint from Keystone's service catalog. `OS_ENDPOINT_TYPE` is also supported;
if both it and `OS_INTERFACE` are set, `OS_ENDPOINT_TYPE` takes precedence.

If your credentials include `OS_STORAGE_URL`, the server authenticates with
Keystone and uses that explicit Swift URL instead of the catalog endpoint. The
URL must identify the intended test account; the server sends the Keystone
token to it. `OS_INTERFACE` and the catalog selection settings do not choose a
different storage endpoint when this URL is set. Make sure `OS_AUTH_TOKEN` is
not set in password mode.

### Pre-issued token

Set `OS_STORAGE_URL` to your test Swift storage URL:

```sh
export OS_STORAGE_URL="https://storage.example.com/v1/your-account"
```

Provide `OS_AUTH_TOKEN` securely from your secret manager. For example, enter
the token at the prompt, then retrieve it without displaying it:

```sh
secret-tool store --label="Swift test token" --collection=default service swift-mcp purpose swift-token
OS_AUTH_TOKEN="$(secret-tool lookup service swift-mcp purpose swift-token)"
export OS_AUTH_TOKEN
```

Do not also set the Keystone password-mode variables above: the server rejects
mixed credential modes. The server does not refresh pre-issued tokens; replace
an expired token and restart it.

### Start

Optionally restrict access to specific containers before starting. For example:

```sh
export SWIFT_MCP_CONTAINERS='["test-container"]'
```

If this variable is unset, callers can access every container the Swift
identity can read. Set it to `[]` to allow none. All callers share the same
Swift identity and allowlist.

Start the server in the foreground:

```sh
uv run --locked swift-mcp-keys start swift-validation
```

Leave this terminal open. The command retrieves the pepper from the keyring,
checks that the Workshop has a valid key, and starts the server at
`http://127.0.0.1:8000/mcp`. Stop it with `Ctrl+C`.

TLS certificate verification for Swift is enabled by default. Supply a trusted
CA using `OS_CACERT` if needed; do not disable verification to work around an
unknown certificate error. External Swift and Keystone endpoints must use
HTTPS; plaintext HTTP is accepted only for loopback test endpoints.

## 4. Connect the included Workshop

Open another host terminal in the repository root. The included example uses
Copilot CLI and needs a separate GitHub Copilot credential. The Copilot token is
not the Swift MCP caller key.

1. In `validation/copilot/workshop.yaml`, change the `copilot-token` lookup
   attributes to match exactly one supported Copilot token in your host
   keyring. The checked-in `username` belongs to a specific account. If you
   need to store your token, set that `username` to a unique label of your
   choice and use the same label in the command below. Enter the token at the
   prompt; do not put its value in the YAML.

   ```sh
   secret-tool store --label="Copilot token" --collection=default service copilot-cli username your-account-label
   ```

   Replace `your-account-label` with the label used in the YAML. See the
   [validation Workshop README](validation/copilot/README.md).
2. Launch the Workshop and connect its token and tunnel:

   ```sh
   workshop -p validation/copilot launch
   workshop -p validation/copilot connect swift-validation/copilot:github-token :copilot-token
   workshop -p validation/copilot connect swift-validation/copilot:swift-mcp :swift-mcp
   workshop -p validation/copilot connections swift-validation
   ```

   Check that both connections appear as `manual`. The tunnel connects the
   Workshop's `127.0.0.1:8000` to the host server; it does not authenticate
   callers.
3. Replace `42` below with the `key_id` printed by `init` or `add`. Start
   Copilot with the caller key retrieved from the host keyring for this run:

   ```sh
   set +x
   KEY_ID=42
   COPILOT_MCP_SWIFT_KEY="$(secret-tool lookup service swift-mcp purpose caller workshop swift-validation key-id "$KEY_ID")" \
     workshop -p validation/copilot run --env COPILOT_MCP_SWIFT_KEY swift-validation -- copilot
   ```

   The action loads `validation/copilot/mcp-config.json`. Copilot expands the
   key from the environment and sends it in the `X-API-Key` header. The lookup
   must match the key created for this Workshop; it must not print the key.
   If the key is missing, the action refuses to start.
4. In Copilot, open `/mcp`, check `swift-mcp`, and ask it to list containers.
   A successful connection and tool call against your test service verify this
   setup; merely launching the Workshop does not.

If you change `SWIFT_MCP_PORT` on the host, update both tunnel endpoints in
`validation/copilot/workshop.yaml` and the URL in
`validation/copilot/mcp-config.json` to the same port. Refresh the Workshop and
reconnect the tunnel after changing its endpoints. Manual connections also need
to be re-established after `workshop restore` or removing and relaunching the
Workshop.

## Connect an existing Workshop

For a different container Workshop, create a separate caller key with
`swift-mcp-keys add <workshop-name>`. Add a tunnel slot to its `system` SDK and
a tunnel plug to the SDK that runs your MCP client. For example, merge these
entries into your existing Workshop definition, replacing `<client-sdk>` with
that SDK's name:

```yaml
sdks:
  - name: system
    slots:
      swift-mcp:
        interface: tunnel
        endpoint: 127.0.0.1:8000
  - name: <client-sdk>
    plugs:
      swift-mcp:
        interface: tunnel
        endpoint: 127.0.0.1:8000
```

Launch the Workshop, or run `workshop refresh` if it already exists. Then connect
the tunnel explicitly:

```sh
workshop connect <workshop-name>/<client-sdk>:swift-mcp :swift-mcp
workshop connections <workshop-name>
```

Configure the client for Streamable HTTP at `http://127.0.0.1:8000/mcp` and
send the Workshop's caller key in the `X-API-Key` header on every request.
Deliver the key through an approved secret mechanism, not the Workshop
definition or an MCP configuration file. For a single command, the
[Workshop secrets guide](https://ubuntu.com/workshop/docs/how-to/develop-with-workshops/provide-secrets.md)
shows how to pass a value from the host keyring using `--env` without placing it
in the command line. See also the [OpenCode MCP configuration guide](https://opencode.ai/docs/mcp-servers).
The verified client setup is Copilot CLI in the included container Workshop;
check connectivity and a tool call when configuring another client or Workshop.

## Available tools and limits

The server provides `get_account_stats`, `list_containers`,
`get_container_stats`, `list_objects`, `get_object_stats`, and `read_object`.
It also exposes the resource template `swift://object/{container}/{object_name}`;
percent-encode special characters in resource URIs. It does not expose Swift
write operations.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `SWIFT_MCP_PORT` | `8000` | Host loopback port |
| `SWIFT_MCP_BYTE_LIMIT` | `1048576` | Maximum bytes per object read |
| `SWIFT_MCP_PAGE_LIMIT` | `1000` | Maximum entries per listing page |
| `SWIFT_MCP_LISTING_BYTE_LIMIT` | `4194304` | Maximum backend listing response bytes |
| `SWIFT_MCP_RATE_LIMIT` | `60` | Requests per caller key per minute |
| `SWIFT_MCP_OPENRC` | unset | Path to an OpenStack RC file supplying Swift authentication |

The numeric limits must be positive integers; the port must also be at most 65535.
Large limits can use substantial memory.
With a container allowlist, `list_containers` returns the configured names, not
an account-wide listing, and `get_account_stats` is unavailable. An allowlisted
name can appear even if the container is missing or unreadable; use
`get_container_stats` to check it. See [ADR 0002](docs/adr/0002-allowlisted-container-listing.md).

## Troubleshooting

- **Server does not start:** Check the Swift credential mode, unlocked keyring,
  and active key with `uv run --locked swift-mcp-keys status swift-validation`.
  The server fails closed if caller authentication is not configured.
- **Client cannot reach the server:** Check that the host process is running,
  both tunnel endpoints and the client URL use the same port, and the tunnel is
  connected with `workshop -p validation/copilot connections swift-validation`.
- **HTTP 401:** Check that the client sends the correct, active Workshop key in
  `X-API-Key`. Do not print the key while troubleshooting.
- **HTTP 503:** The server allows up to eight active requests and rejects excess
  requests with `Server busy`. Ask the agent to make fewer calls in parallel and
  retry failed calls with backoff. Uvicorn also has a separate limit of 32 open
  connections or tasks, which includes idle keep-alive connections; hitting it
  logs `Exceeded concurrency limit`.
- **Workshop launch or connection fails:** Inspect
  `workshop -p validation/copilot changes` and then
  `workshop -p validation/copilot tasks <change-id>`.

## Security notes

The server listens only on host loopback. Keep the tunnel local and use a
separate, revocable caller key for each Workshop. A tunnel is not an
authentication boundary. Any process in a Workshop can request a connected
Workshop secret; the per-run Swift key is available to Copilot and its child
processes. Disconnect interfaces when they are not needed and review agent
permissions before running an agent. Swift object contents are untrusted and may
be sent to the agent's model provider.

The MCP interface is read-only, but it does not remove write privileges from
the underlying Swift identity. Protect the host keyring, key database, and
logs, and use a read-only Swift identity. The documented local GNOME Keyring
AES-128-CBC exception does not authenticate ciphertext; an
authenticated-encryption store is safer. See [key management](docs/key-management.md)
for storage and rotation limitations.

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

The pytest suite includes Hypothesis property tests. To run just those tests:

```sh
uv run --locked pytest -q tests/test_properties.py
```

Hypothesis prints a minimal failing example when it finds a counterexample and
stores examples locally in `.hypothesis/`. To reproduce a run with a known seed,
add `--hypothesis-seed=<seed>` to the pytest command. Add a regression test or
an explicit example for any important failure; the local example database is
not shared with CI.
