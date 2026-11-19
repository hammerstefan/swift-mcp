#!/usr/bin/env bash
set +x
set -euo pipefail

if [[ ! ${KEY_ID:-} =~ ^[1-9][0-9]{0,18}$ ]]; then
    printf '%s\n' 'Set KEY_ID to the numeric key_id printed when you created the quickstart caller key.' >&2
    exit 1
fi

unset COPILOT_MCP_SWIFT_KEY caller_key "${!OS_@}" "${!ST_@}" "${!SWIFT_MCP_@}" "${!SWIFTCLIENT_@}"
if ! caller_key=$(secret-tool lookup service swift-mcp purpose caller \
    workshop swift-mcp-quickstart key-id "$KEY_ID" 2>/dev/null) || [[ -z $caller_key ]]; then
    printf '%s\n' 'Could not retrieve the quickstart caller key. Unlock the host keyring and check KEY_ID.' >&2
    exit 1
fi

COPILOT_MCP_SWIFT_KEY=$caller_key exec workshop -p quickstart run --env COPILOT_MCP_SWIFT_KEY -- copilot
