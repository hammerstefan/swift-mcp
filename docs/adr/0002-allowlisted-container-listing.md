# Page allowlisted container listings from the allowlist, and hide account totals

Status: accepted.

When `SWIFT_MCP_CONTAINERS` is set, `list_containers` returns pages built from the sorted allowlist itself, filtered by the caller's `prefix` and `marker`. It does not list the Swift account. `get_account_stats` returns an error while an allowlist is set, because account totals include excluded containers. This narrows story 19 of the spec.

An earlier draft listed the account through Swift and filtered the results. Its `next_marker` could then be the name of an excluded container, so it was replaced with an AES-256-GCM cursor, which needed a separate `SWIFT_MCP_CURSOR_KEY` secret. That design still made an account-wide request on every call. Pages could also come back empty and still carry a cursor, so a caller could page through the whole account. Paging from the allowlist removes the account request, the cursor key and the empty pages.

Consequences: with an allowlist, container entries carry only `name`. Containers that are allowlisted but missing or unreadable in Swift still appear in the list, and later reads of them fail with a safe error. Callers that need object counts or byte totals use `get_container_stats`. Without an allowlist, `list_containers` still passes the native Swift marker through unchanged.
