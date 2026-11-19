#!/usr/bin/env bash
set +x
set -euo pipefail

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

workshop_cmd() {
    workshop -p quickstart "$@"
}

port_ready() {
    (: </dev/tcp/127.0.0.1/8000) 2>/dev/null
}

[[ -n ${SWIFT_MCP_CONTAINERS:-} ]] || fail 'Set SWIFT_MCP_CONTAINERS to your test container allowlist.'
[[ ${SWIFT_MCP_PORT-8000} == 8000 ]] || fail 'The quickstart Workshop requires SWIFT_MCP_PORT=8000.'
if port_ready; then
    fail 'Port 127.0.0.1:8000 is already in use.'
fi

client=swift-mcp-quickstart/copilot
server_pid=
tunnel_connected=0
token_connected=0

cleanup() {
    local result=$?
    trap '' HUP INT TERM
    if [[ -n $server_pid ]]; then
        kill "$server_pid" 2>/dev/null || true
        wait "$server_pid" 2>/dev/null || true
    fi
    if (( token_connected )); then
        workshop_cmd disconnect "$client:github-token" || result=1
    fi
    if (( tunnel_connected )); then
        workshop_cmd disconnect "$client:swift-mcp" || result=1
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

uv run --locked swift-mcp-keys start swift-mcp-quickstart &
server_pid=$!
# Keep Swift configuration out of the Workshop CLI's environment.
unset "${!OS_@}" "${!ST_@}" "${!SWIFT_MCP_@}" "${!SWIFTCLIENT_@}"

for (( attempt = 0; attempt < 100; attempt++ )); do
    kill -0 "$server_pid" 2>/dev/null || fail 'Swift MCP exited before it was ready.'
    if port_ready; then
        break
    fi
    sleep 0.1
done
(( attempt < 100 )) || fail 'Swift MCP did not listen on 127.0.0.1:8000 in time.'

if info=$(workshop_cmd info 2>/dev/null); then
    if grep -qxE 'status:[[:blank:]]+stopped[[:blank:]]*' <<< "$info"; then
        workshop_cmd start
    elif ! grep -qxE 'status:[[:blank:]]+ready[[:blank:]]*' <<< "$info"; then
        fail 'The quickstart Workshop is not ready or stopped. Check workshop -p quickstart info.'
    fi
else
    workshop_cmd launch
fi

kill -0 "$server_pid" 2>/dev/null || fail 'Swift MCP stopped during Workshop startup.'
workshop_cmd connect "$client:swift-mcp" :swift-mcp
tunnel_connected=1
kill -0 "$server_pid" 2>/dev/null || fail 'Swift MCP stopped before the Copilot token was connected.'
workshop_cmd connect "$client:github-token" :copilot-token
token_connected=1
workshop_cmd connections
printf '%s\n' 'Open the second terminal to run Copilot. Press Ctrl+C here to stop Swift MCP and disconnect.'
wait "$server_pid"
