# API-key component review (development selection, not deployment approval)

Type: research
Status: claimed

The host MCP caller credential is separate from the Swift identity. No unauthenticated production HTTP mode is permitted. This review selects `generateapikey==0.6.0` as the **candidate** verifier and hashed-key SQLite store for implementation; deployment remains blocked until the complete integration and release gates below pass. Its repository has existed since 2023, but the authentication integration and key store are recent (0.5.0 and 0.6.0 respectively); no independent external security audit was found. Do not equate this source review with one.

## Source review (2026-11-17)

- [Tagged 0.6.0 source](https://github.com/nuhmanpk/generate-api-key/tree/v0.6.0.0/generateapikey), [PyPI release](https://pypi.org/project/generateapikey/0.6.0/), [changelog](https://github.com/nuhmanpk/generate-api-key/blob/main/python/CHANGELOG.md). Check the installed wheel against this release before approval.
- `integrations.APIKeyAuth` extracts a key from `X-API-Key`, optionally Bearer or a query parameter; the integration must disable both optional inputs. It rejects malformed and excessive-length keys, hashes the candidate, looks up only its hash, and checks revocation/expiry. No raw key is stored in `KeyStore`.
- `KeyStore` uses parameterized SQL, permits independent Workshop keys and revocation, and supports overlapping rotation. Its `rotate()` commits shortening the old key's lifetime before inserting the new one; an insert failure may interrupt rotation. Do not treat rotation as atomic. Store permissions and backups are the operator's responsibility.
- **Not suitable as a drop-in ASGI guard:** the Starlette `APIKeyBackend` passes requests with no key through as anonymous. FastMCP routes do not individually carry Starlette's `@requires` decorator. Use a fail-closed, pure-ASGI whole-app gate that calls the maintained `APIKeyAuth` verifier on every MCP request, including tools, resources, initialization, GET and DELETE. No `BaseHTTPMiddleware` (SSE buffering). Do not implement key verification ourselves.
- The library's optional failed-attempt limiter is IP-keyed and does not count missing-key requests. Add an independent bounded request-rate, concurrency and request-time guard, including unauthenticated traffic, and a per-key rate limit. Do not trust a forwarded IP header supplied by the caller.
- Configure header-only extraction, a nonempty key store, a secret-manager-provided pepper, exact Host/Origin allowlists and a loopback listener. Refuse startup if any required setting is missing, invalid, or the database cannot be opened safely. No public storage routes. Avoid logging credentials, URL query strings, Swift response headers or object bytes.

## Before deployment

1. Verify installed wheel, pin dependencies, and complete threat-focused security review of the chosen version and its integration, including secret delivery and rotation under realistic restarts/failures.
2. Exercise 401/403/429 and revocation at the **public MCP HTTP boundary**, for each tool/resource and all supported protocol revisions; test streaming, host/origin rejection, invalid input and rate/concurrency limits.
3. Perform the separate upstream conformance matrix, controlled Swift/Keystone tests, and actual Workshop/OpenCode/Copilot CLI smoke tests. Do not claim release readiness before they pass.

The [previously assessed `fastapi-key-auth` middleware](https://github.com/iwpnd/fastapi-key-auth/blob/main/src/fastapi_key_auth/middleware/authorizer.py) remains rejected by the plan because of ordinary key equality and regex-public-route bypass risk. FastMCP's `StaticTokenVerifier` is development-only; its GitHub OAuth fallback defaults to Fernet-encrypted storage, which does not meet the approved AES-256-GCM-only rule without a separately reviewed storage integration.

## Comments

- 2026-11-18: The host gate now enforces a global unauthenticated rate limit, a per-key rate limit, an 8-request concurrency cap (HTTP 503), a 30-second request time limit (HTTP 504) and a 4 MiB body cap that counts actual bytes, including chunked bodies (HTTP 413). Items 1 and 3 under "Before deployment" remain open.
- 2026-11-18: The asynchronous request deadline now includes authentication and request-body receipt, not only downstream application handling. A timeout after response headers have been sent cuts off the response; real-client streaming behavior still needs verification.
- 2026-11-18: The operator accepted AES-128-CBC for local GNOME Keyring secret storage as an exception to the AES-256-GCM-only policy. CBC alone does not authenticate ciphertext; an independently reviewed store using authenticated encryption is safer. Verify the deployed keyring configuration and its secret-delivery and rotation behavior; this decision does not close item 1 or approve the verifier.
- 2026-11-18: An experimental local key manager stages new caller keys disabled until keyring delivery is verified, and supports operator-confirmed rotation with up to 24 hours of overlap. Review its pinned dependency's SQLite schema use, recovery, keyring delivery and startup behavior independently before deployment. Offline pepper rotation and interrupted-initialization recovery are not implemented.
