# Copilot validation Workshop

This is a separate Workshop project for testing a host-local Swift MCP server,
not a development environment for the server. Use `validation/copilot` as the
Workshop project directory: only this directory is mounted at `/project`.
The host runs the server and retains the Swift credentials.

## Prerequisites

- Workshop 0.9.8 or later, with a working host Secret Service and the
  `copilot-cli` keyring entry identified by the attributes in `workshop.yaml`.
  If using another account, change the lookup attributes to match exactly one
  item before launch. The Copilot SDK requires a supported Copilot token.
- A separately provisioned, revocable Swift MCP caller key for this Workshop.
  The Copilot token is not a Swift MCP key. Follow the
  [quickstart](../../docs/quickstart.md) for a first-time setup with test data.
- A server listening on `127.0.0.1:8000` on the host. If its port changes,
  update both tunnel endpoints in `workshop.yaml` and the URL in
  `mcp-config.json` to the same port. The server validates the HTTP Host.

Copilot CLI connectivity to Swift MCP through this Workshop has been verified.

## Launch and connect

From the repository root:

```sh
workshop -p validation/copilot launch
workshop -p validation/copilot connect swift-validation/copilot:github-token :copilot-token
workshop -p validation/copilot connect swift-validation/copilot:swift-mcp :swift-mcp
workshop -p validation/copilot connections swift-validation
```

Both connections are manual. The token slot resolves the existing host keyring
item on demand; the tunnel forwards the Workshop's loopback port to host
loopback. A tunnel does not authenticate callers. Manual connections survive a
refresh unless their relevant definitions change, but must be reconnected after
`workshop restore` or removing and relaunching the Workshop.

## Run Copilot

Store the separate Swift MCP caller key in your host secret manager. For the
following examples, the default host keyring contains exactly one item with
`service=swift-mcp-validation` (use a different lookup if appropriate):

```sh
secret-tool store --label="Swift MCP validation key" --collection=default service swift-mcp-validation
```

The command prompts for the key without putting it in the command line. Do not
put either credential in project files, command arguments, or shell history.
Run the interactive client from the repository root:

```sh
COPILOT_MCP_SWIFT_KEY="$(secret-tool lookup service swift-mcp-validation)" \
  workshop -p validation/copilot run --env COPILOT_MCP_SWIFT_KEY swift-validation -- copilot
```

The action refuses to start if the Swift key is missing. In Copilot, use `/mcp`
to inspect `swift-mcp` and try a read-only tool. For a non-interactive run,
pass Copilot CLI arguments after the action name, for example:

```sh
COPILOT_MCP_SWIFT_KEY="$(secret-tool lookup service swift-mcp-validation)" \
  workshop -p validation/copilot run --env COPILOT_MCP_SWIFT_KEY swift-validation -- copilot \
  -p 'Use swift-mcp to list the available containers.'
```

The action supplies `mcp-config.json` for that session and forwards arguments;
Copilot expands the key from the environment into the HTTP header. Non-interactive
tool permissions and the chosen prompt are the operator's responsibility. To
verify your setup, check that an actual call to `swift-mcp` succeeds.

## Security boundaries

Any process in the Workshop can request the connected Copilot token, and the
Swift key is visible to Copilot and its child processes during a run. The
project mount is writable and outbound networking is available. Review agent
permissions and disconnect secrets and the tunnel when they are not needed;
do not use unattended bypass modes without accepting those risks. Swift object
contents are untrusted data and may be sent to Copilot's provider: use approved
test data and never treat object content as instructions.
