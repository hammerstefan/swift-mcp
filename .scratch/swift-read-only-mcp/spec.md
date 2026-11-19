# Read-only Swift MCP server — specification

Status: claimed

Published in the local Markdown issue tracker. Implementation and public-HTTP tests are in progress. Selected conformance results are recorded in `docs/conformance.md`; a CI job has been added but has not yet been verified on GitHub. Copilot CLI connectivity to Swift MCP through the included container Workshop has been verified.

## Problem Statement

A person using OpenCode or GitHub Copilot CLI inside a container Workshop cannot safely inspect their OpenStack Swift storage through MCP while keeping their Swift identity on the host. A useful solution must make account, container and object reads available without offering storage mutations, bound the data returned to an agent, use the same selected environment-based Swift credentials as swiftclient, and prove its MCP behavior at both supported protocol revisions. A Workshop tunnel alone does not authenticate its callers; an unsecured host-local service would expose the shared Swift identity's readable data to any process that can reach it.

## Solution

Run a Python MCP server on the same host as the container Workshop. Give OpenCode and Copilot CLI access to a loopback-only Streamable HTTP endpoint through a manually connected Workshop tunnel. Authenticate every MCP data request using an approved in-process Python API-key component, with a separate revocable credential for each Workshop; if no component passes security review, use an approved GitHub OAuth proxy with an explicit caller allowlist instead. The host server uses its configured Swift identity to return a bounded, read-only view through MCP tools and an object resource template. Test the public server boundary and the relevant official conformance scenarios for both 2025-11-25 and 2026-07-28; treat actual client connectivity as a release gate rather than an assumption.

## User Stories

1. As a Workshop operator, I want the MCP server to run on the host, so that the Swift identity does not have to be placed inside the Workshop.
2. As a Workshop operator, I want to connect my container Workshop to the host-local MCP endpoint explicitly, so that a tunnel is not opened silently.
3. As an OpenCode user, I want to connect to the server through the Workshop tunnel, so that I can inspect Swift storage from my development session.
4. As a Copilot CLI user, I want the same read-only storage access, so that I can use either client in the Workshop.
5. As a Workshop operator, I want each Workshop to have its own revocable MCP credential, so that I can withdraw one Workshop's access without affecting others.
6. As a Workshop operator, I want the MCP credential supplied through a secret-management path rather than committed to a project, so that it is not exposed in source control.
7. As a Workshop operator, I want documentation stating that a connected Workshop secret is available to other processes in that Workshop, so that I understand the actual trust boundary.
8. As an MCP caller, I want every tool and resource request authenticated, so that another reachable process cannot read Swift data without permission.
9. As a Workshop operator, I want the server to refuse startup when MCP credentials or their verifier are missing, so that an auth configuration error does not create an open endpoint.
10. As a Workshop operator, I want a reviewed GitHub OAuth fallback if safe in-process API-key verification cannot be established, so that failed auth research does not lead to an insecure shortcut.
11. As a Workshop operator using the GitHub fallback, I want only explicitly approved GitHub user IDs admitted, so that an arbitrary GitHub account cannot read my Swift data.
12. As a Swift operator, I want to use Keystone v3 password environment variables supported by swiftclient, so that I can reuse an existing Swift configuration.
13. As a Swift operator, I want to use a pre-issued storage URL and auth token instead, so that I can reuse that documented swiftclient environment mode.
14. As a Swift operator, I want an error if both complete credential modes are configured, so that leftover variables cannot silently select the wrong Swift identity.
15. As a Swift operator, I want incomplete or absent credentials rejected with a safe diagnostic, so that storage reads do not proceed with uncertain identity.
16. As a Swift operator, I want configured region, endpoint and certificate settings interpreted consistently with the supported swiftclient mode, so that the server reaches the intended storage endpoint securely.
17. As a Swift operator, I want TLS verification enabled unless I explicitly choose swiftclient's insecure setting, so that certificate failures are not bypassed accidentally.
18. As a Swift operator, I want an unmistakable warning if I disable TLS verification, so that I see the risk of that exception.
19. As an authorized MCP caller, I want account statistics, so that I can understand the Swift account without modifying it.
20. As an authorized MCP caller, I want a paged list of containers, so that I can discover storage without fetching the entire account.
21. As an authorized MCP caller, I want container statistics, so that I can inspect a container before reading objects.
22. As an authorized MCP caller, I want a paged list of objects in a container, so that large containers remain navigable.
23. As an authorized MCP caller, I want prefix and applicable delimiter filters with Swift-native continuation markers, so that I can narrow and continue listings without a server-side full scan.
24. As an authorized MCP caller, I want object statistics, so that I can inspect its size and content type before reading bytes.
25. As an authorized MCP caller, I want a bounded read of a named object at a chosen byte offset, so that I can inspect large objects one part at a time.
26. As an authorized MCP caller, I want readable text returned without byte replacement, so that I can trust the contents I inspect.
27. As an authorized MCP caller, I want binary and non-UTF-8 data encoded losslessly with content and range metadata, so that the response does not corrupt object bytes.
28. As an authorized MCP caller, I want a template for addressing a named object as an MCP resource, so that compatible clients can read small objects directly.
29. As an authorized MCP caller, I want an oversized resource read rejected with a pointer to the ranged tool, so that a resource request never silently truncates content.
30. As an authorized MCP caller, I want only the current object version, so that the read surface does not unexpectedly expose historical versions.
31. As an authorized MCP caller, I want names containing slashes treated as Swift object names, so that an object is not mistaken for a host filesystem path.
32. As a Swift operator, I want an optional container allowlist enforced across listings, statistics, tools and resources, so that MCP access can be narrower than the Swift identity's scope.
33. As a Swift operator, I want an unset container allowlist to mean the identity's full readable Swift scope, so that the server can be used without listing every container explicitly.
34. As a Swift operator, I want an explicitly empty container allowlist to deny all containers, so that I can deliberately disable data access.
35. As a Swift operator, I want a nonempty allowlist to expose only named containers, so that a caller cannot bypass it by supplying a direct object or resource URI.
36. As an MCP caller, I want safe, reviewed account/container/object metadata rather than raw headers, so that signing keys and other secrets are not returned accidentally.
37. As a Swift operator, I want the server to use only read operations against Swift, so that no MCP tool can upload, copy, delete, change metadata or generate TempURLs.
38. As a Swift operator, I want finite request size, time and concurrency controls, so that untrusted or oversized objects cannot consume unbounded server resources.
39. As a Swift operator, I want the object limit and page limit configurable with defaults of 1 MiB and 1,000 entries, so that I can adapt them to my environment while seeing the resource-cost risk of larger values.
40. As an MCP caller, I want safe errors for missing objects, invalid inputs, permission failures and rate limits, so that I can respond without seeing host secrets or object bytes in diagnostics.
41. As a security reviewer, I want the server to enforce the configured byte limit even when HEAD and GET disagree or Swift ignores a range, so that the limit is a real boundary rather than a hint.
42. As a security reviewer, I want object content treated as untrusted data rather than agent instructions, so that storage content cannot silently change the server's behavior.
43. As a maintainer, I want tests at the public MCP HTTP boundary against controlled Swift and Keystone endpoints, so that observable authorization and storage behavior is proved without coupling tests to internal functions.
44. As a maintainer, I want applicable official server conformance scenarios executed independently for both MCP revisions, so that a pass on one wire format is not mistaken for a pass on the other.
45. As a maintainer, I want fixed-name conformance sample handlers isolated from production, so that protocol fixtures never become data-access features for real callers.
46. As a maintainer, I want the exact selected conformance scenarios and exclusions published with results, so that a partial capability-relevant run is not misrepresented as a full-suite pass.
47. As a maintainer, I want a real OpenCode and Copilot CLI smoke test through a container Workshop, so that the clients' actual protocol negotiation and authentication determine whether the release is usable.
48. As a maintainer, I want uv, Ruff, ty and Python tests run in CI, so that packaging, style, types and observable behavior are checked consistently.

## Implementation Decisions

- Use one shared Swift identity for all authenticated MCP callers; do not delegate or impersonate callers in Swift. Swift authorization still applies to every underlying storage read.
- Run a Python host service using FastMCP v4 on the MCP Python SDK v2. Expose Streamable HTTP on a loopback-only endpoint. Configure a manual host-service tunnel for container Workshops. Do not rely on the tunnel for MCP caller authentication.
- Explicitly support both MCP revisions 2025-11-25 and 2026-07-28. Preserve their distinct lifecycle and wire behavior as supplied by the library; do not add application session state that assumes an initialize handshake.
- Make safe selection of a maintained, in-process ASGI API-key authentication component a blocking prerequisite. Require header-only credentials, per-Workshop revocation, secret-manager integration, request-level fail-closed enforcement, safe comparison, a narrowly controlled public-route policy, streaming compatibility, rotation and rate limits. Reject startup without a valid MCP authentication configuration; never implement an ad-hoc login or key-verification protocol.
- If no component passes review, use FastMCP's GitHub OAuth proxy with a registered OAuth App and a mandatory nonempty allowlist of stable GitHub user IDs. Confirm the whole flow with each client before using it for Swift data. If that path fails, stop for a new design decision rather than disable authentication.
- Maintain two independent auth domains: the MCP caller credential authorizes access to this server; the host's Keystone or pre-issued Swift credential authorizes storage access. Never expose the Swift token, password or storage URL through an MCP response.
- Support the documented Keystone v3 password and pre-issued token/storage-URL environment modes selected in the plan. Reject incomplete or simultaneously complete modes. Preserve the documented region, endpoint, CA, and client certificate settings. Reject redirects during Keystone authentication in either password mode. A pre-issued token's renewal is the operator's responsibility.
- Preserve the explicit swiftclient insecure-TLS opt-in for compatibility, but use verified TLS by default and warn conspicuously whenever the exception is active. Do not allow arbitrary Swift request headers, URLs or methods to be supplied by callers.
- Provide account, container and object statistics; paged container/object listings; and bounded, offset-based current-object reads. Enforce `GET` and `HEAD` as the only methods at the Swift storage transport and reject redirects. Listings use native markers and relevant prefix/delimiter filters; never use full listings or implicit cross-container scans. While a container allowlist is configured, page the container list from the allowlist itself (names only, filtered by the same prefix and marker) instead of listing the account, so no excluded name can be returned and no account-wide listing is made. See ADR 0002.
- Default to 1 MiB of object content and 1,000 entries per listing page. Permit any positive finite operator-configured values with no built-in upper ceiling, as explicitly decided; enforce the selected values on actual streamed bytes and document the risk of excessive configuration.
- Stream listing JSON responses and enforce a configurable positive response-byte cap (default 4 MiB). Do not accept compressed listing responses; reject oversized or malformed responses with a safe tool error.
- Return text only after strict UTF-8 decoding of recognized text media; encode other bytes as base64 with response metadata. Validate inputs and encode them for their output context. Treat object names as opaque Swift identifiers rather than filesystem paths. Reject standalone `.` and `..` segments before building a request path; also reject `/` in container names.
- Expose a named-object resource template but no account-wide resource enumeration. Resource reads must use the same auth, container policy and byte cap as tools, and reject oversized objects instead of returning a partial object.
- Return a reviewed allowlist of harmless metadata fields; never return raw Swift headers, account/container signing keys, arbitrary user metadata, object bytes in logs or internal exception details.
- Container policy is optional: unset means all containers allowed by Swift; explicitly empty means deny all; populated means only exact named containers. Check the policy for account-derived listings and every direct container/object tool or resource read. Do not expose account-wide aggregate statistics while an allowlist is configured, because those totals include excluded containers (story 19 scope reduction; see ADR 0002).
- Additional hardening beyond the stories, recorded so it is reviewed rather than implied: refuse plaintext Swift endpoints other than loopback and endpoint URLs with credentials, query or fragment; accept only recognized `SWIFTCLIENT_INSECURE` values; map `OS_CERT`/`OS_KEY` for client certificates; make the loopback port configurable with `SWIFT_MCP_PORT`; and send `no-store`, `nosniff`, `no-referrer` and a deny-all CSP on every response.
- Require a dedicated Swift identity whose effective Swift policy and ACLs deny writes. The operator verifies those permissions before deploying; startup can validate credential configuration but cannot establish effective Swift policy. A read-only MCP interface does not lower the authority of the Swift credential itself.

## Testing Decisions

- **Primary seam:** launch the server and assert observable MCP-over-HTTP responses while controlled Swift/Keystone endpoints supply known data and failures. This is the highest practical seam in an empty repository and avoids internal-method assertions or test-only production APIs. There is no existing test prior art to copy.
- Cover both Swift credential paths, ambiguous and missing credentials, region and TLS configuration, and pre-issued-token expiration at the process boundary. Verify no real secrets or live storage are needed in automated tests.
- Pin the exact MCP tool set and check that all Swift storage handlers use only `GET` and `HEAD` and refuse redirects. Verify both password modes refuse Keystone redirects.
- Check authenticating and authorizing *every* tool and resource operation: missing/invalid/revoked key, distinct Workshop keys, missing and expired tokens where applicable, unset/empty/populated container allowlist, direct URI/name bypass attempts, rate limiting, startup refusal, secret-safe errors and redacted logs.
- Test list pages, native markers, prefixes and delimiters against controlled accounts including empty and large listings. Assert page bounds and absence of implicit full-account scans.
- Test content limits at boundaries: zero/negative/oversized ranges, objects changing between HEAD and GET, ignored Range responses, incomplete streams, non-UTF-8 text, binary content, names with slashes and escaped URI characters, and parity between tool and resource access. Assert only the selected safe metadata fields appear.
- Run the upstream conformance runner against a separate credential-free, loopback-only fixture that registers its required fixed-name sample handlers using the same MCP stack. Keep fixture handlers absent from production. Pin runner version, run selected applicable server scenarios at both dated protocol revisions, report exact scenario and check outcomes, and never baseline away a failure. The default complete server suite includes unrelated features and is not the promised pass criterion.
- Exercise real Swift read behavior against a controlled Swift service separately from the protocol fixture. Record client versions when running manual or automated host/Workshop tunnel smoke tests. Copilot CLI connectivity through the included Workshop has been verified separately.
- Run Python tests, Ruff and ty in CI. Favor user-visible output and security invariants over assertions about library internals or helper-call counts.

## Out of Scope

- Swift writes of any kind: upload, delete, copy, metadata/ACL changes, or TempURL creation.
- Historical object-version listing and version-specific reads.
- Keystone v1/v2, `clouds.yaml`, per-caller Swift impersonation, and cross-container content search.
- An unauthenticated production HTTP mode, a home-built OAuth server, or a development-only static-token verifier for real Swift data.
- Workshop VM tunnel connectivity, non-loopback public hosting, browser-facing storage UI, and persistent account-wide MCP resource enumeration.
- A claim of full default MCP conformance-suite success for unrelated optional or fixed-fixture capabilities.

## Further Notes

- **Issue-tracker record:** this file is the canonical local Markdown spec. Its `Status:` line records the triage state.
- **Security and deployment checks:** Complete external review and managed provisioning/rotation; test per-request authorization, streaming, rate limits, Host/Origin checks and safe logging. The GitHub fallback needs a secret manager, approved-user list, safe OAuth callback state/CSRF handling and a real client test. Run non-root and keep security headers and cookie settings appropriate to the eventual auth choice. No credentials or object bytes belong in issue text or test artifacts.
- **Accepted risk exceptions:** an unset container allowlist exposes the Swift identity's full readable scope; `SWIFTCLIENT_INSECURE` can explicitly disable certificate verification; and raised output limits have no built-in ceiling. For local secret storage, the operator accepts the GNOME Keyring's AES-128-CBC as an exception to the AES-256-GCM-only policy. CBC alone does not authenticate ciphertext; a secret store using authenticated encryption is the safer alternative. Default TLS verification and bounded default outputs do not erase these risks.
- **Sources and rationale:** the repository planning contract, domain glossary and host/Workshop architecture decision are the source for this specification. Upstream Swift, FastMCP, MCP conformance, Workshop, OpenCode and Copilot CLI documentation is linked from the planning contract; verify pinned versions and current upstream behavior before implementation.
