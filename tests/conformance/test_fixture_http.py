"""Capability enforcement at the isolated fixture's public HTTP boundary."""

import pytest
from fixture import create_app
from starlette.testclient import TestClient


@pytest.mark.parametrize("capabilities", [{}, {"roots": {}}])
def test_missing_sampling_capability_returns_protocol_error(capabilities):
    with TestClient(create_app(), base_url="http://127.0.0.1:8765") as client:
        response = call_sampling_tool(client, capabilities)

    assert response.status_code == 400, response.text
    payload = response.json()
    assert payload["id"] == "missing-sampling"
    assert payload["error"]["code"] == -32021
    assert payload["error"]["data"]["requiredCapabilities"] == {"sampling": {}}
    assert "result" not in payload


def test_declared_sampling_capability_allows_input_request():
    with TestClient(create_app(), base_url="http://127.0.0.1:8765") as client:
        response = call_sampling_tool(client, {"sampling": {}})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["id"] == "missing-sampling"
    assert payload["result"]["resultType"] == "input_required"
    assert payload["result"]["inputRequests"]["sample"]["method"] == "sampling/createMessage"


def call_sampling_tool(client, capabilities):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "missing-sampling",
            "method": "tools/call",
            "params": {
                "name": "test_missing_capability",
                "arguments": {},
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": capabilities,
                },
            },
        },
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/call",
            "Mcp-Name": "test_missing_capability",
        },
    )
