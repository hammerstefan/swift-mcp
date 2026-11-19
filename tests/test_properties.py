"""Generated invariants for the read-only Swift view."""

import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from generateapikey.keystore import KeyStore
from hypothesis import example, given, settings
from hypothesis import strategies as st
from starlette.testclient import TestClient

from swift_read_only_mcp.config import bounded_text
from swift_read_only_mcp.server import _object_payload, create_app
from swift_read_only_mcp.swift import SwiftBackend


@st.composite
def allowlist_cases(draw):
    # Include Unicode and punctuation, but only names accepted by the server.
    name = st.text(alphabet="abc-é中😀", min_size=1, max_size=8)
    names = draw(st.lists(name, unique=True, max_size=8))
    limit = draw(st.integers(min_value=1, max_value=5))
    candidates = names + [n[:1] for n in names]
    selector = st.one_of(st.none(), name, st.sampled_from(candidates)) if candidates else st.none()
    return names, limit, draw(selector), draw(selector)


@settings(max_examples=30, deadline=None)
@example(([], 1, None, None))
@example((["a", "aa", "ab", "b"], 2, "a", None))
@example((["a", "é", "中", "😀"], 1, None, "a"))
@given(allowlist_cases())
def test_allowlist_pages_expose_only_matching_names_without_swift(case):
    names, limit, prefix, marker = case
    expected = sorted(
        name
        for name in names
        if (prefix is None or name.startswith(prefix)) and (marker is None or name > marker)
    )

    # Every generated example gets fresh authentication, rate limiting, and app state.
    with TemporaryDirectory() as directory:
        database = Path(directory) / "keys.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = str(store.create("property-test", prefix="smcp", min_entropy=128).key)
        app = create_app(
            {
                "SWIFT_MCP_KEY_DB": str(database),
                "SWIFT_MCP_KEY_PEPPER": "test-pepper",
                "SWIFT_MCP_CONTAINERS": json.dumps(names),
                "OS_AUTH_TOKEN": "fixture-swift-token",
                "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
            }
        )
        with patch.object(
            SwiftBackend, "list", side_effect=AssertionError("unexpected listing")
        ) as listing:
            with TestClient(app, base_url="http://127.0.0.1:8000") as client:
                seen = []
                for start in range(0, len(expected) + 1, limit):
                    arguments = {"limit": limit, "prefix": prefix, "marker": marker}
                    response = client.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {
                                "name": "list_containers",
                                "arguments": arguments,
                                "_meta": {
                                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                    "io.modelcontextprotocol/clientCapabilities": {},
                                },
                            },
                        },
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2026-07-28",
                            "Mcp-Method": "tools/call",
                            "Mcp-Name": "list_containers",
                            "X-API-Key": key,
                        },
                    )
                    assert response.status_code == 200
                    result = response.json()["result"]["structuredContent"]
                    page = expected[start : start + limit]
                    next_marker = page[-1] if start + limit < len(expected) else None
                    assert result == {
                        "containers": [{"name": name} for name in page],
                        "next_marker": next_marker,
                    }
                    seen.extend(item["name"] for item in result["containers"])
                    marker = result["next_marker"]
                    if marker is None:
                        break
                assert seen == expected
            listing.assert_not_called()


@example(value="é" * 128, byte_limit=256)
@example(value="é" * 129, byte_limit=256)
@example(value="😀" * 256, byte_limit=1024)
@example(value="😀" * 257, byte_limit=1024)
@example(value="\ud800", byte_limit=256)
@example(value="a\x00b", byte_limit=256)
@example(value="", byte_limit=256)
@given(
    value=st.text(
        alphabet=st.sampled_from(["a", "/", "é", "中", "😀", "\x00", "\ud800"]), max_size=300
    ),
    byte_limit=st.integers(min_value=1, max_value=1024),
)
def test_bounded_text_accepts_only_nonempty_utf8_without_nul_within_byte_limit(value, byte_limit):
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        expected = False
    else:
        expected = bool(value) and "\x00" not in value and len(encoded) <= byte_limit
    assert bounded_text(value, byte_limit) is expected


@example(data=b"\xff", content_type="text/plain", offset=0, remaining=0)
@example(data="é".encode(), content_type="text/plain", offset=1, remaining=1)
@given(
    data=st.one_of(st.binary(max_size=128), st.text(max_size=64).map(str.encode)),
    content_type=st.sampled_from(["text/plain", "application/json", "application/octet-stream"]),
    offset=st.integers(min_value=0, max_value=128),
    remaining=st.integers(min_value=0, max_value=128),
)
def test_object_payload_preserves_bytes_and_range_metadata(data, content_type, offset, remaining):
    size = offset + len(data) + remaining
    payload = _object_payload(data, content_type, size, offset)
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        text = None

    if content_type != "application/octet-stream" and text is not None:
        assert payload["encoding"] == "utf-8"
        assert payload["data"] == text
    else:
        assert payload["encoding"] == "base64"
        assert payload["data"] == base64.b64encode(data).decode("ascii")
    assert payload["content_type"] == content_type
    assert payload["size"] == size
    assert payload["offset"] == offset
    assert payload["partial"] is (remaining > 0)
