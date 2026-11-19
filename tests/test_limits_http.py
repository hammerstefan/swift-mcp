"""Request limits, input checks and edge-case reads through the public HTTP boundary."""

import asyncio
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import quote, urlsplit

import httpx
import pytest
from generateapikey.keystore import KeyStore
from starlette.testclient import TestClient

from swift_read_only_mcp.server import create_app


@contextmanager
def swift_service(handler):
    service = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        yield service
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)


def app_and_key(tmp_path, port, **settings):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": f"http://127.0.0.1:{port}/v1/AUTH_test",
            **settings,
        }
    )
    return app, str(key)


def call(client, key, method, params):
    name = params.get("name") or params.get("uri")
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {
                **params,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            },
        },
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": method,
            "Mcp-Name": name,
            "X-API-Key": key,
        },
    )


def call_tool(client, key, name, arguments):
    return call(client, key, "tools/call", {"name": name, "arguments": arguments})


class Quiet(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def test_requests_beyond_the_concurrency_limit_are_refused(tmp_path):
    release = threading.Event()

    class SlowSwift(Quiet):
        def do_HEAD(self):
            release.wait(timeout=5)
            self.send_response(204)
            self.send_header("X-Account-Container-Count", "1")
            self.end_headers()

    with swift_service(SlowSwift) as service:
        app, key = app_and_key(tmp_path, service.server_port)
        app.max_concurrency = 1
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            results = {}
            first = Thread(
                target=lambda: results.update(first=call_tool(client, key, "get_account_stats", {}))
            )
            first.start()
            deadline = time.monotonic() + 5
            while app.active_requests == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            second = call_tool(client, key, "get_account_stats", {})
            release.set()
            first.join(timeout=5)

    assert second.status_code == 503
    assert second.json() == {"error": "Server busy"}
    assert results["first"].status_code == 200


def test_request_exceeding_the_time_limit_gets_a_timeout_response(tmp_path):
    release = threading.Event()

    class StalledSwift(Quiet):
        def do_HEAD(self):
            release.wait(timeout=5)
            self.send_response(204)
            self.end_headers()

    with swift_service(StalledSwift) as service:
        app, key = app_and_key(tmp_path, service.server_port)
        app.request_timeout = 0.2
        try:
            with TestClient(app, base_url="http://127.0.0.1:8000") as client:
                response = call_tool(client, key, "get_account_stats", {})
        finally:
            release.set()

    assert response.status_code == 504
    assert response.json() == {"error": "Request timed out"}


def test_slow_request_body_times_out_and_releases_the_request_slot(tmp_path):
    app, key = app_and_key(tmp_path, 1)
    app.max_concurrency = 1
    app.request_timeout = 0.05

    async def stalled_request():
        async def body():
            yield b'{"jsonrpc":'
            await asyncio.sleep(1)
            yield b'"2.0"}'

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            return await asyncio.wait_for(
                client.post("/mcp", content=body(), headers={"X-API-Key": key}), timeout=0.5
            )

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = asyncio.run(stalled_request())
        assert response.status_code == 504
        assert response.json() == {"error": "Request timed out"}
        subsequent = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/list",
                "X-API-Key": key,
            },
        )
        assert subsequent.status_code == 200


@pytest.mark.parametrize(
    ("name", "arguments", "error"),
    [
        ("list_containers", {"prefix": "a\x00b"}, "Invalid prefix"),
        ("list_containers", {"marker": "m" * 257}, "Invalid marker"),
        ("list_objects", {"container": "c", "prefix": "p" * 1025}, "Invalid prefix"),
        ("list_objects", {"container": "c", "marker": "a\x00"}, "Invalid marker"),
        ("list_objects", {"container": "c", "delimiter": "//"}, "Invalid delimiter"),
        ("list_objects", {"container": "c", "delimiter": "\x00"}, "Invalid delimiter"),
    ],
)
def test_listing_filters_are_validated_before_reaching_swift(tmp_path, name, arguments, error):
    requests_seen = []

    class RecordingSwift(Quiet):
        def do_GET(self):
            requests_seen.append(self.path)
            self.send_response(204)
            self.end_headers()

    with swift_service(RecordingSwift) as service:
        app, key = app_and_key(tmp_path, service.server_port)
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            response = call_tool(client, key, name, arguments)

    assert response.json()["result"]["isError"] is True
    assert error in response.text
    assert requests_seen == []


def test_unsafe_storage_names_are_rejected_without_contacting_swift(tmp_path):
    requests_seen = []

    class RecordingSwift(Quiet):
        def parse_request(self):
            if not super().parse_request():
                return False
            requests_seen.append(self.command)
            self.send_error(500)
            return False

    with swift_service(RecordingSwift) as service:
        app, key = app_and_key(tmp_path, service.server_port, SWIFT_MCP_CONTAINERS='["visible"]')
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            for container in (".", "..", "two/parts"):
                response = call_tool(client, key, "get_container_stats", {"container": container})
                assert response.json()["result"]["isError"] is True
                assert "Invalid container name" in response.text

            for object_name in (".", "..", "logs/./report", "logs/../report"):
                arguments = {"container": "visible", "object_name": object_name}
                for tool in ("get_object_stats", "read_object"):
                    response = call_tool(client, key, tool, arguments)
                    assert response.json()["result"]["isError"] is True
                    assert "Invalid object name" in response.text
            uri = "swift://object/visible/" + quote("logs/../report", safe="")
            response = call(client, key, "resources/read", {"uri": uri})
            assert "Invalid object name" in response.json()["error"]["message"]

    assert requests_seen == []


@pytest.mark.parametrize("name", [".", "..", "two/parts"])
def test_unsafe_container_allowlist_is_rejected_at_startup(tmp_path, name):
    with pytest.raises(ValueError, match="Invalid container allowlist"):
        app_and_key(tmp_path, 1, SWIFT_MCP_CONTAINERS=f'["{name}"]')


def test_all_storage_handlers_use_only_get_or_head_and_refuse_redirects(tmp_path):
    requests_seen = []

    class RedirectingSwift(Quiet):
        def parse_request(self):
            if not super().parse_request():
                return False
            requests_seen.append((self.command, urlsplit(self.path).path))
            if self.command not in ("GET", "HEAD"):
                self.send_error(405)
                return False
            return True

        def do_HEAD(self):
            if self.path.endswith("/small.txt"):
                self.send_response(200)
                self.send_header("Content-Length", "1")
                self.end_headers()
            else:
                self.do_GET()

        def do_GET(self):
            if self.path == "/not-the-storage-endpoint":
                self.send_error(404)
                return
            self.send_response(307)
            self.send_header(
                "Location", f"http://localhost:{service.server_port}/not-the-storage-endpoint"
            )
            self.send_header("Content-Length", "0")
            self.end_headers()

    container = {"container": "visible"}
    obj = {**container, "object_name": "nested/report.txt"}
    with swift_service(RedirectingSwift) as service:
        app, key = app_and_key(tmp_path, service.server_port)
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            for tool, arguments in (
                ("get_account_stats", {}),
                ("list_containers", {}),
                ("get_container_stats", container),
                ("list_objects", container),
                ("get_object_stats", obj),
                ("read_object", obj),
            ):
                response = call_tool(client, key, tool, arguments)
                assert response.json()["result"]["isError"] is True
                assert "fixture-swift-token" not in response.text
            for uri in (
                "swift://object/visible/nested%2Freport.txt",
                "swift://object/visible/small.txt",
            ):
                response = call(client, key, "resources/read", {"uri": uri})
                assert "Swift resource read failed" in response.json()["error"]["message"]

    assert requests_seen == [
        ("HEAD", "/v1/AUTH_test"),
        ("GET", "/v1/AUTH_test"),
        ("HEAD", "/v1/AUTH_test/visible"),
        ("GET", "/v1/AUTH_test/visible"),
        ("HEAD", "/v1/AUTH_test/visible/nested/report.txt"),
        ("GET", "/v1/AUTH_test/visible/nested/report.txt"),
        ("HEAD", "/v1/AUTH_test/visible/nested/report.txt"),
        ("HEAD", "/v1/AUTH_test/visible/small.txt"),
        ("GET", "/v1/AUTH_test/visible/small.txt"),
    ]


def test_read_of_an_empty_object_returns_empty_data(tmp_path):
    class EmptyObjectSwift(Quiet):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "0")
            self.end_headers()

    with swift_service(EmptyObjectSwift) as service:
        app, key = app_and_key(tmp_path, service.server_port)
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            response = call_tool(
                client, key, "read_object", {"container": "c", "object_name": "empty.txt"}
            )

    assert response.json()["result"]["structuredContent"] == {
        "encoding": "utf-8",
        "data": "",
        "content_type": "text/plain",
        "size": 0,
        "offset": 0,
        "partial": False,
    }


def test_read_past_the_end_of_an_object_returns_empty_data(tmp_path):
    class ShortObjectSwift(Quiet):
        def do_GET(self):
            self.send_response(416)
            self.send_header("Content-Range", "bytes */8")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "8")
            self.end_headers()

    with swift_service(ShortObjectSwift) as service:
        app, key = app_and_key(tmp_path, service.server_port)
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            response = call_tool(
                client,
                key,
                "read_object",
                {"container": "c", "object_name": "o.bin", "offset": 8},
            )

    assert response.json()["result"]["structuredContent"] == {
        "encoding": "base64",
        "data": "",
        "content_type": "application/octet-stream",
        "size": 8,
        "offset": 8,
        "partial": False,
    }


def test_object_reads_request_only_the_current_version(tmp_path):
    requests_seen = []

    class RecordingSwift(Quiet):
        def do_HEAD(self):
            requests_seen.append((self.command, urlsplit(self.path)))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2")
            self.end_headers()

        def do_GET(self):
            requests_seen.append((self.command, urlsplit(self.path)))
            self.send_response(206)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Range", "bytes 0-1/2")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    with swift_service(RecordingSwift) as service:
        app, key = app_and_key(tmp_path, service.server_port)
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            target = {"container": "c", "object_name": "o.txt"}
            call_tool(client, key, "get_object_stats", target)
            call_tool(client, key, "read_object", target)

    assert [command for command, _ in requests_seen] == ["HEAD", "GET"]
    for _, url in requests_seen:
        assert url.path == "/v1/AUTH_test/c/o.txt"
        assert url.query == ""


def test_resource_rejects_a_chunked_object_shorter_than_its_head_size(tmp_path):
    class TruncatingSwift(Quiet):
        protocol_version = "HTTP/1.1"

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "8")
            self.end_headers()

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"4\r\npart\r\n0\r\n\r\n")
            self.close_connection = True

    with swift_service(TruncatingSwift) as service:
        app, key = app_and_key(tmp_path, service.server_port)
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            response = call(client, key, "resources/read", {"uri": "swift://object/c/o.txt"})

    assert "Swift returned an incomplete object" in response.text
    assert "part" not in response.text
