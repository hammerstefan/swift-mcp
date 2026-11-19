"""Swift responses observed through the authenticated MCP-over-HTTP boundary."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest
from generateapikey.keystore import KeyStore
from starlette.testclient import TestClient

from swift_read_only_mcp.server import create_app


def test_account_statistics_do_not_expose_swift_secret_headers(tmp_path, monkeypatch, caplog):
    requests_seen = []

    class SwiftFixture(BaseHTTPRequestHandler):
        def do_HEAD(self):
            requests_seen.append((self.command, self.path, self.headers.get("X-Auth-Token")))
            self.send_response(204)
            self.send_header("X-Account-Container-Count", "3")
            self.send_header("X-Account-Object-Count", "18")
            self.send_header("X-Account-Bytes-Used", "240")
            self.send_header("X-Account-Meta-Temp-URL-Key", "do-not-leak")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        monkeypatch.setenv("SWIFT_MCP_KEY_DB", str(database))
        monkeypatch.setenv("SWIFT_MCP_KEY_PEPPER", "test-pepper")
        monkeypatch.setenv("OS_AUTH_TOKEN", "fixture-swift-token")
        monkeypatch.setenv("OS_STORAGE_URL", f"http://127.0.0.1:{service.server_port}/v1/AUTH_test")

        with caplog.at_level("DEBUG", logger="swiftclient"):
            with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
                response = client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "tools/call",
                        "params": {
                            "name": "get_account_stats",
                            "arguments": {},
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
                        "Mcp-Name": "get_account_stats",
                        "X-API-Key": str(key),
                    },
                )

        assert response.status_code == 200, response.text
        assert '"container_count":3' in response.text
        assert '"bytes_used":240' in response.text
        assert "do-not-leak" not in response.text
        assert "fixture-swift-token" not in caplog.text
        assert "fixture-s" not in caplog.text
        assert "do-" not in caplog.text
        assert requests_seen == [("HEAD", "/v1/AUTH_test", "fixture-swift-token")]
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


def test_allowlisted_container_pages_never_list_the_account(tmp_path, monkeypatch):
    requests_seen = []

    class SwiftFixture(BaseHTTPRequestHandler):
        def do_GET(self):
            requests_seen.append(self.path)
            self.send_response(500)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        for name, value in {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "SWIFT_MCP_CONTAINERS": '["logs-b", "logs-a", "other", "logs-c"]',
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": f"http://127.0.0.1:{service.server_port}/v1/AUTH_test",
        }.items():
            monkeypatch.setenv(name, value)

        def list_page(client, request_id, arguments):
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
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
                    "X-API-Key": str(key),
                },
            )
            assert response.status_code == 200, response.text
            return response.json()["result"]["structuredContent"]

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            first = list_page(client, 1, {"limit": 2, "prefix": "logs-"})
            second = list_page(client, 2, {"limit": 2, "prefix": "logs-", "marker": "logs-b"})

        assert first == {
            "containers": [{"name": "logs-a"}, {"name": "logs-b"}],
            "next_marker": "logs-b",
        }
        assert second == {"containers": [{"name": "logs-c"}], "next_marker": None}
        assert requests_seen == []
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


def test_direct_container_statistics_cannot_bypass_allowlist(tmp_path, monkeypatch):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    for name, value in {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "SWIFT_MCP_CONTAINERS": "[]",
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
    }.items():
        monkeypatch.setenv(name, value)

    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "get_container_stats",
                    "arguments": {"container": "secret"},
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
                "Mcp-Name": "get_container_stats",
                "X-API-Key": str(key),
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert "not accessible" in response.text
    assert "Unknown tool" not in response.text


def test_empty_allowlist_does_not_expose_account_statistics(tmp_path, monkeypatch):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    for name, value in {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "SWIFT_MCP_CONTAINERS": "[]",
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
    }.items():
        monkeypatch.setenv(name, value)

    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "get_account_stats",
                    "arguments": {},
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
                "Mcp-Name": "get_account_stats",
                "X-API-Key": str(key),
            },
        )

    assert response.status_code == 200
    assert "Container not accessible" in response.text


def test_direct_object_listing_cannot_bypass_allowlist(tmp_path, monkeypatch):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    for name, value in {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "SWIFT_MCP_CONTAINERS": "[]",
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": "http://127.0.0.1:1/v1/AUTH_test",
    }.items():
        monkeypatch.setenv(name, value)

    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "list_objects",
                    "arguments": {"container": "secret"},
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
                "Mcp-Name": "list_objects",
                "X-API-Key": str(key),
            },
        )

    assert response.status_code == 200
    assert "Container not accessible" in response.text
    assert "Unknown tool" not in response.text


def test_object_listing_uses_swift_filters_and_preserves_subdir_marker(tmp_path, monkeypatch):
    requests_seen = []

    class SwiftFixture(BaseHTTPRequestHandler):
        def do_GET(self):
            requests_seen.append(urlsplit(self.path))
            body = (
                b'[{"name":"folder/a.txt","bytes":3,"content_type":"text/plain",'
                b'"last_modified":"2026-01-01T00:00:00Z","hash":"etag",'
                b'"x-object-meta-secret":"hide"},'
                b'{"subdir":"folder/sub/","x-object-meta-secret":"hide"}]'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        for name, value in {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": f"http://127.0.0.1:{service.server_port}/v1/AUTH_test",
        }.items():
            monkeypatch.setenv(name, value)

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "list_objects",
                        "arguments": {
                            "container": "visible",
                            "limit": 2,
                            "marker": "older-name",
                            "prefix": "folder/",
                            "delimiter": "/",
                        },
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
                    "Mcp-Name": "list_objects",
                    "X-API-Key": str(key),
                },
            )

        assert response.status_code == 200, response.text
        result = response.json()["result"]["structuredContent"]
        assert result["objects"] == [
            {
                "name": "folder/a.txt",
                "bytes": 3,
                "content_type": "text/plain",
                "last_modified": "2026-01-01T00:00:00Z",
                "hash": "etag",
            },
            {"subdir": "folder/sub/"},
        ]
        assert result["next_marker"] == "folder/sub/"
        assert "hide" not in response.text
        query = parse_qs(requests_seen[0].query)
        assert query["limit"] == ["2"]
        assert query["marker"] == ["older-name"]
        assert query["prefix"] == ["folder/"]
        assert query["delimiter"] == ["/"]
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize(
    ("tool_name", "arguments", "error"),
    [
        ("list_containers", {"limit": 100}, "Swift container listing failed"),
        ("list_objects", {"container": "visible", "limit": 100}, "Swift object listing failed"),
    ],
)
def test_listing_response_byte_limit_rejects_oversized_backend_body(
    tmp_path, monkeypatch, tool_name, arguments, error
):
    rows = [{"name": f"entry-{index:02d}", "count": index} for index in range(10)]
    body = json.dumps(rows).encode()

    class SwiftFixture(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for offset in range(0, len(body), 32):
                chunk = body[offset : offset + 32]
                self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        for name, value in {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "SWIFT_MCP_LISTING_BYTE_LIMIT": "64",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": f"http://127.0.0.1:{service.server_port}/v1/AUTH_test",
        }.items():
            monkeypatch.setenv(name, value)

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
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
                    "Mcp-Name": tool_name,
                    "X-API-Key": str(key),
                },
            )

        assert error in response.text
        assert "entry-00" not in response.text
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


def test_swift_cannot_exceed_requested_object_page_size(tmp_path, monkeypatch):
    class SwiftFixture(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'[{"name":"a"},{"name":"b"},{"name":"c"}]'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        for name, value in {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": f"http://127.0.0.1:{service.server_port}/v1/AUTH_test",
        }.items():
            monkeypatch.setenv(name, value)

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "list_objects",
                        "arguments": {"container": "visible", "limit": 2},
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
                    "Mcp-Name": "list_objects",
                    "X-API-Key": str(key),
                },
            )

        assert "oversized object page" in response.text
        assert '"name":"c"' not in response.text
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


def test_ranged_object_read_encodes_invalid_utf8_losslessly(tmp_path, monkeypatch):
    ranges = []

    class SwiftFixture(BaseHTTPRequestHandler):
        def do_GET(self):
            ranges.append((self.path, self.headers.get("Range")))
            self.send_response(206)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Range", "bytes 2-3/8")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"\xff\x00")

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        for name, value in {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "SWIFT_MCP_CONTAINERS": '["visible"]',
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": f"http://127.0.0.1:{service.server_port}/v1/AUTH_test",
        }.items():
            monkeypatch.setenv(name, value)

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "read_object",
                        "arguments": {
                            "container": "visible",
                            "object_name": "nested/raw.bin",
                            "offset": 2,
                            "length": 2,
                        },
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
                    "Mcp-Name": "read_object",
                    "X-API-Key": str(key),
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["result"]["isError"] is False
        assert '"encoding":"base64"' in response.text
        assert '"data":"/wA="' in response.text
        assert ranges == [("/v1/AUTH_test/visible/nested/raw.bin", "bytes=2-3")]
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


def test_ranged_object_read_rejects_swift_ignoring_range(tmp_path, monkeypatch):
    requests_seen = []

    class SwiftFixture(BaseHTTPRequestHandler):
        def do_GET(self):
            requests_seen.append((self.path, self.headers.get("Range")))
            body = b"must-not-be-returned"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        for name, value in {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "SWIFT_MCP_BYTE_LIMIT": "4",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": f"http://127.0.0.1:{service.server_port}/v1/AUTH_test",
        }.items():
            monkeypatch.setenv(name, value)

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "read_object",
                        "arguments": {
                            "container": "visible",
                            "object_name": "nested/raw.txt",
                            "offset": 0,
                            "length": 4,
                        },
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
                    "Mcp-Name": "read_object",
                    "X-API-Key": str(key),
                },
            )

        assert "Swift did not honor the byte range" in response.text
        assert "must-not-be-returned" not in response.text
        assert requests_seen == [("/v1/AUTH_test/visible/nested/raw.txt", "bytes=0-3")]
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


def test_ranged_object_read_rejects_incomplete_response_body(tmp_path, monkeypatch):
    class SwiftFixture(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(206)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Range", "bytes 0-3/8")
            self.send_header("Content-Length", "4")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"ab")
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        for name, value in {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "SWIFT_MCP_BYTE_LIMIT": "4",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": f"http://127.0.0.1:{service.server_port}/v1/AUTH_test",
        }.items():
            monkeypatch.setenv(name, value)

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "read_object",
                        "arguments": {
                            "container": "visible",
                            "object_name": "nested/raw.txt",
                            "offset": 0,
                            "length": 4,
                        },
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
                    "Mcp-Name": "read_object",
                    "X-API-Key": str(key),
                },
            )

        assert "Swift returned an invalid byte range" in response.text
        assert "ab" not in response.text
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


def test_object_statistics_exclude_private_metadata(tmp_path, monkeypatch):
    paths = []

    class SwiftFixture(BaseHTTPRequestHandler):
        def do_HEAD(self):
            paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "8")
            self.send_header("Content-Type", "text/plain")
            self.send_header("ETag", '"abc"')
            self.send_header("X-Object-Meta-Password", "never-return-this")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        for name, value in {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": f"http://127.0.0.1:{service.server_port}/v1/AUTH_test",
        }.items():
            monkeypatch.setenv(name, value)

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "get_object_stats",
                        "arguments": {"container": "visible", "object_name": "nested/thing.txt"},
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
                    "Mcp-Name": "get_object_stats",
                    "X-API-Key": str(key),
                },
            )

        assert response.status_code == 200
        assert '"size":8' in response.text
        assert "never-return-this" not in response.text
        assert paths == ["/v1/AUTH_test/visible/nested/thing.txt"]
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize(
    (
        "identity",
        "region",
        "endpoint_type",
        "selected_account",
        "storage_override",
        "interface",
        "auth_suffix",
    ),
    [
        ("name", "RegionOne", "publicURL", "public-one", False, None, "/v3"),
        ("name", "RegionTwo", "internalURL", "internal-two", False, None, "/v3"),
        ("id", "RegionTwo", "publicURL", "public-two", False, None, "/v3"),
        ("name", "MissingRegion", "publicURL", None, False, None, "/v3"),
        pytest.param(
            "name",
            "MissingRegion",
            None,
            "explicit-account",
            True,
            "public",
            "/v3",
            id="storage-override",
        ),
        pytest.param(
            "name", "RegionTwo", None, "internal-two", False, "internal", "/v3", id="interface"
        ),
        pytest.param(
            "name", "RegionTwo", None, "public-two", False, "public", "/v3", id="public-interface"
        ),
        pytest.param(
            "name",
            "MissingRegion",
            "publicURL",
            "explicit-account",
            True,
            None,
            "",
            id="versionless-auth-url",
        ),
        pytest.param(
            "name",
            "MissingRegion",
            "publicURL",
            "explicit-account",
            True,
            None,
            "/v3.0",
            id="version-3-0-auth-url",
        ),
        pytest.param(
            "name",
            "RegionTwo",
            "publicURL",
            "public-two",
            False,
            None,
            "/v3.0",
            id="version-3-0-catalog-auth-url",
        ),
        pytest.param(
            "id",
            "MissingRegion",
            "publicURL",
            "explicit-account",
            True,
            None,
            "/v3",
            id="storage-override-ids",
        ),
        pytest.param(
            "name",
            "RegionTwo",
            "internalURL",
            "internal-two",
            False,
            "public",
            "/v3",
            id="endpoint-type-precedence",
        ),
        pytest.param(
            "name",
            "UnauthorizedRegion",
            "publicURL",
            None,
            True,
            None,
            "/v3",
            id="storage-override-auth-failure",
        ),
        pytest.param(
            "name",
            "RedirectRegion",
            "publicURL",
            None,
            True,
            None,
            "/v3",
            id="storage-override-auth-redirect",
        ),
    ],
)
def test_keystone_v3_password_authenticates_before_swift_read(
    tmp_path,
    monkeypatch,
    identity,
    region,
    endpoint_type,
    selected_account,
    storage_override,
    interface,
    auth_suffix,
):
    requests_seen = []

    class KeystoneAndSwiftFixture(BaseHTTPRequestHandler):
        def do_GET(self):
            requests_seen.append((self.command, self.path, self.headers.get("X-Auth-Token")))
            if self.path.startswith("/v1/"):
                body = b'[{"name":"visible","count":1,"bytes":2}]'
            else:
                body = json.dumps(
                    {
                        "version": {
                            "id": "v3.0",
                            "status": "stable",
                            "links": [
                                {
                                    "rel": "self",
                                    "href": f"http://127.0.0.1:{service.server_port}/v3/",
                                }
                            ],
                        }
                    }
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests_seen.append((self.command, self.path, json.loads(body)))
            if self.path not in ("/v3/auth/tokens", "/v3.0/auth/tokens"):
                self.send_response(404)
                self.end_headers()
                return
            if region == "UnauthorizedRegion":
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if region == "RedirectRegion":
                self.send_response(307)
                self.send_header("Location", f"http://127.0.0.1:{service.server_port}/redirect")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            payload = json.dumps(
                {
                    "token": {
                        "methods": ["password"],
                        "expires_at": "2030-01-01T00:00:00Z",
                        "project": {
                            "id": "project-id",
                            "name": "swift-project",
                            "domain": {"id": "d", "name": "Default"},
                        },
                        "user": {
                            "id": "user-id",
                            "name": "swift-user",
                            "domain": {"id": "d", "name": "Default"},
                        },
                        "catalog": [
                            {
                                "type": "object-store",
                                "name": "swift",
                                "id": "swift-id",
                                "endpoints": [
                                    {
                                        "interface": interface,
                                        "region": endpoint_region,
                                        "id": account,
                                        "url": f"http://127.0.0.1:{service.server_port}/v1/{account}",
                                    }
                                    for interface, endpoint_region, account in (
                                        ("public", "RegionOne", "public-one"),
                                        ("internal", "RegionTwo", "internal-two"),
                                        ("public", "RegionTwo", "public-two"),
                                    )
                                ],
                            }
                        ],
                    }
                }
            ).encode()
            self.send_response(201)
            self.send_header("X-Subject-Token", "keystone-token")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_HEAD(self):
            requests_seen.append((self.command, self.path, self.headers.get("X-Auth-Token")))
            self.send_response(204)
            self.send_header("X-Account-Container-Count", "3")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), KeystoneAndSwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        for name in (
            "OS_AUTH_TOKEN",
            "OS_STORAGE_URL",
            "OS_USERNAME",
            "OS_USER_ID",
            "OS_USER_DOMAIN_NAME",
            "OS_USER_DOMAIN_ID",
            "OS_PROJECT_NAME",
            "OS_PROJECT_ID",
            "OS_PROJECT_DOMAIN_NAME",
            "OS_PROJECT_DOMAIN_ID",
            "OS_ENDPOINT_TYPE",
            "OS_INTERFACE",
            "ST_AUTH_VERSION",
            "OS_AUTH_VERSION",
            "OS_IDENTITY_API_VERSION",
        ):
            monkeypatch.delenv(name, raising=False)
        for name, value in {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            ("OS_IDENTITY_API_VERSION" if storage_override else "ST_AUTH_VERSION"): "3",
            "OS_AUTH_URL": f"http://127.0.0.1:{service.server_port}{auth_suffix}",
            "OS_PASSWORD": "private-password",
            "OS_REGION_NAME": region,
            **({"OS_ENDPOINT_TYPE": endpoint_type} if endpoint_type else {}),
            **({"OS_INTERFACE": interface} if interface else {}),
            **(
                {"OS_STORAGE_URL": f"http://127.0.0.1:{service.server_port}/v1/explicit-account"}
                if storage_override
                else {}
            ),
            **(
                {
                    "OS_USERNAME": "swift-user",
                    "OS_USER_DOMAIN_NAME": "Default",
                    "OS_PROJECT_NAME": "swift-project",
                    "OS_PROJECT_DOMAIN_NAME": "Default",
                }
                if identity == "name"
                else {"OS_USER_ID": "user-id", "OS_PROJECT_ID": "project-id"}
            ),
        }.items():
            monkeypatch.setenv(name, value)

        if storage_override and identity == "name" and region == "MissingRegion" and interface:
            rc = tmp_path / "test-openrc"
            rc.write_text(
                f'export OS_AUTH_URL="http://127.0.0.1:{service.server_port}/v3"\n'
                'export OS_USERNAME="swift-user"\n'
                'export OS_PASSWORD="private-password"\n'
                'export OS_TENANT_NAME="swift-project"\n'
                "export OS_PROJECT_NAME=${OS_TENANT_NAME}\n"
                "export OS_USER_DOMAIN_NAME=Default\n"
                "export OS_PROJECT_DOMAIN_NAME=Default\n"
                "export OS_IDENTITY_API_VERSION=3\n"
                "export OS_REGION_NAME=MissingRegion\n"
                "export OS_INTERFACE=public\n"
                f'export OS_STORAGE_URL="http://127.0.0.1:{service.server_port}/v1/explicit-account"\n',
                encoding="utf-8",
            )
            monkeypatch.setenv("SWIFT_MCP_OPENRC", str(rc))
            monkeypatch.setenv("OS_AUTH_TOKEN", "ignored-ambient-token")

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "get_account_stats",
                        "arguments": {},
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
                    "Mcp-Name": "get_account_stats",
                    "X-API-Key": str(key),
                },
            )
            listing = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {
                        "name": "list_containers",
                        "arguments": {"limit": 2},
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
                    "X-API-Key": str(key),
                },
            )

        assert response.status_code == 200, response.text
        assert listing.status_code == 200, listing.text
        auth_requests = [item[2]["auth"] for item in requests_seen if item[0] == "POST"]
        assert len(auth_requests) == 2
        for auth in auth_requests:
            assert auth["identity"]["methods"] == ["password"]
            user = auth["identity"]["password"]["user"]
            project = auth["scope"]["project"]
            if identity == "name":
                assert user["name"] == "swift-user"
                assert user["domain"]["name"] == "Default"
                assert project["name"] == "swift-project"
                assert project["domain"]["name"] == "Default"
            else:
                assert user["id"] == "user-id"
                assert project["id"] == "project-id"
        if selected_account is None:
            assert response.json()["result"]["isError"] is True
            assert listing.json()["result"]["isError"] is True
            assert not any(item[1].startswith("/v1/") for item in requests_seen)
            assert "private-password" not in response.text + listing.text
            assert "keystone-token" not in response.text + listing.text
            return

        assert '"container_count":3' in response.text, [item[:2] for item in requests_seen]
        assert listing.json()["result"]["structuredContent"]["containers"] == [
            {"name": "visible", "count": 1, "bytes": 2}
        ]
        assert ("HEAD", f"/v1/{selected_account}", "keystone-token") in requests_seen
        assert any(
            item[0] == "GET"
            and item[1].startswith(f"/v1/{selected_account}?")
            and item[2] == "keystone-token"
            for item in requests_seen
        )
        assert all(
            not item[1].startswith("/v1/") or item[1].startswith(f"/v1/{selected_account}")
            for item in requests_seen
        )
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("head_size, methods_expected", [(5, ["HEAD"]), (2, ["HEAD", "GET"])])
def test_oversized_object_resource_rejects_head_get_disagreement(
    tmp_path, monkeypatch, head_size, methods_expected
):
    methods = []

    class SwiftFixture(BaseHTTPRequestHandler):
        def do_HEAD(self):
            methods.append(self.command)
            self.send_response(200)
            self.send_header("Content-Length", str(head_size))
            self.end_headers()

        def do_GET(self):
            methods.append(self.command)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "5")
            self.end_headers()
            self.wfile.write(b"abcde")

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        for name, value in {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "SWIFT_MCP_BYTE_LIMIT": "4",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": f"http://127.0.0.1:{service.server_port}/v1/AUTH_test",
        }.items():
            monkeypatch.setenv(name, value)

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "resources/read",
                    "params": {
                        "uri": "swift://object/visible/nested%2Flarge.txt",
                        "_meta": {
                            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                            "io.modelcontextprotocol/clientCapabilities": {},
                        },
                    },
                },
                headers={
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "resources/read",
                    "Mcp-Name": "swift://object/visible/nested%2Flarge.txt",
                    "X-API-Key": str(key),
                },
            )

        assert "read_object" in response.text
        assert "abcde" not in response.text
        assert methods == methods_expected
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


def test_small_text_resource_reads_the_complete_named_object(tmp_path, monkeypatch):
    paths = []

    class SwiftFixture(BaseHTTPRequestHandler):
        def do_HEAD(self):
            paths.append((self.command, self.path))
            self.send_response(200)
            self.send_header("Content-Length", "3")
            self.end_headers()

        def do_GET(self):
            paths.append((self.command, self.path))
            self.send_response(200)
            self.send_header("Content-Length", "3")
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"yes")

        def log_message(self, format, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        for name, value in {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": f"http://127.0.0.1:{service.server_port}/v1/AUTH_test",
        }.items():
            monkeypatch.setenv(name, value)

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "resources/read",
                    "params": {
                        "uri": "swift://object/visible/nested%2Fsmall.txt",
                        "_meta": {
                            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                            "io.modelcontextprotocol/clientCapabilities": {},
                        },
                    },
                },
                headers={
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "resources/read",
                    "Mcp-Name": "swift://object/visible/nested%2Fsmall.txt",
                    "X-API-Key": str(key),
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["result"]["contents"][0]["text"] == "yes"
        assert paths == [
            ("HEAD", "/v1/AUTH_test/visible/nested/small.txt"),
            ("GET", "/v1/AUTH_test/visible/nested/small.txt"),
        ]
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)
