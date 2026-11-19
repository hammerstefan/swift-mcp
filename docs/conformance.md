# Selected MCP server conformance — fixture results (updated 2026-11-18)

**Not a full-suite pass, not a production-server auth test, not a release approval.** The fixture in `tests/conformance/fixture.py` is credential-free, loopback-only, and never registered on the Swift server. It uses FastMCP 4.1.3 and MCP Python SDK 2.3.0, the same protocol stack as the production entry point. The runner is pinned to [modelcontextprotocol/conformance `96a6f9478b7f2e125cf0360a213383cf57533893`](https://github.com/modelcontextprotocol/conformance/commit/96a6f9478b7f2e125cf0360a213383cf57533893) (`0.2.0-alpha.12`), built from source with Node 22.23.5. The published npm package 0.1.16 did not support the 2026 revision, so it was not used for these results.

Selected scenarios (each run with `--scenario` and the indicated `--spec-version`, no expected-failures file). The `server-stateless` row was reverified on 2026-11-18; the other rows retain their 2026-11-17 results:

| Revision | Scenario | Scored checks | Outcome |
| --- | --- | --- | --- |
| 2025-11-25 | `server-initialize` | `server-initialize`, `wire-schema-valid` | 2/2 pass (`server-session-id-visible-ascii` INFO) |
| 2025-11-25 | `ping` | `ping`, `wire-schema-valid` | 2/2 pass |
| 2025-11-25 | `tools-list` | `tools-list`, `tools-name-format`, `wire-schema-valid` | 3/3 pass |
| 2025-11-25 | `tools-call-simple-text` | `tools-call-simple-text`, `wire-schema-valid` | 2/2 pass |
| 2025-11-25 | `resources-templates-read` | `resources-templates-read`, `wire-schema-valid` | 2/2 pass |
| 2025-11-25 | `dns-rebinding-protection` | `localhost-host-rebinding-rejected`, `localhost-host-valid-accepted` | 2/2 pass |
| 2026-07-28 | `tools-list` | `tools-list`, `tools-name-format`, `wire-schema-valid` | 3/3 pass (`tools-list-deterministic-order` INFO) |
| 2026-07-28 | `tools-call-simple-text` | `tools-call-simple-text`, `wire-schema-valid` | 2/2 pass |
| 2026-07-28 | `resources-templates-read` | `resources-templates-read`, `wire-schema-valid` | 2/2 pass |
| 2026-07-28 | `dns-rebinding-protection` | `localhost-host-rebinding-rejected`, `localhost-host-valid-accepted` | 2/2 pass |
| 2026-07-28 | `server-stateless` | 25 scored checks; see below | **25/25 pass, 0 fail, 0 warnings** |

On 2026-11-18, the pinned runner first reproduced **23/25 pass, 2 fail**: `sep-2575-server-rejects-undeclared-capability` and `sep-2575-missing-capability-http-400`. The fixture's `test_missing_capability` tool returned an `InputRequiredResult` containing `sampling/createMessage` without checking the client's declared sampling capability. The fixture now checks `ctx.session.client_capabilities` before returning that request and raises the SDK's `MCPError` with typed `MissingRequiredClientCapabilityErrorData` when sampling is absent. The existing transport returns HTTP 400 / JSON-RPC `-32021` with `error.data.requiredCapabilities: {"sampling": {}}`, preserving the request ID. No production code or transport workaround was needed.

After the fixture fix, the same command reported **25/25 pass, 0 failed, 0 warnings**, including SUCCESS for both formerly failing check IDs. Five subscription-related checks remain skipped by the runner, as before; no checks were baselined, suppressed, or newly skipped. The exact before/after command, run from the pinned runner checkout using Node 22.23.5, was:

```sh
npm exec --yes --package=node@22 -- node dist/index.js server --url http://127.0.0.1:8765/mcp --scenario server-stateless --spec-version 2026-07-28
```

Focused HTTP regression coverage in `tests/conformance/test_fixture_http.py` verifies absent sampling, unrelated capabilities without sampling, and declared `sampling: {}`. The first two cases failed with HTTP 200 before the fix and now receive the required protocol error; the declared-capability case retains its sampling input request. Verification: `uv run --locked pytest -q tests/conformance/test_fixture_http.py` (**3 passed**), `uv run --locked pytest -q` (**54 passed**, 12 Keystone dependency deprecation warnings), `uv run --locked ruff check src tests`, `uv run --locked ruff format --check tests/conformance`, and `uv run --locked ty check src tests` all passed.

Excluded from this *selected capability-relevant matrix*: sample image/audio/mixed-content handlers, prompt handlers, subscription notifications, client sampling/elicitation, optional extensions/tasks, browser/OAuth provider scenarios, and the complete server requirement sets. These are not Swift data-access features; exclusions do not assert that the server passes those scenarios. The production server additionally requires its own authenticated MCP HTTP tests and a real Workshop/OpenCode/Copilot CLI smoke test, neither of which these fixture results establish.

## Continuous integration

The `conformance` job in `.github/workflows/ci.yml` reruns all eleven rows above on every push and pull request. It checks out the runner at the pinned commit with `persist-credentials: false`, installs with `npm ci --ignore-scripts` on Node 22.23.5, starts the fixture on loopback, and fails the build when any scenario fails. The job has read-only repository permissions and no secrets; the vulnerabilities reported by the runner's dependency install are therefore limited to a throwaway runner with no credentials. On 2026-11-18 the same commands, run locally, passed every row (the 2026 `tools-list` row now scores 4/4, because the runner scores `tools-list-deterministic-order`).

To reproduce: check out the pinned runner commit, install dependencies and build with Node 22+, start `uv run --locked python tests/conformance/fixture.py` in a separate terminal, then use `node dist/index.js server --url http://127.0.0.1:8765/mcp --scenario <name> --spec-version <revision>` from the runner checkout for each row. Keep the fixture on loopback; never use the production Swift endpoint for fixed-name sample scenarios. The runner's pinned dependency install reported vulnerabilities, including one critical, so the CI job runs without secrets or write permissions.
