"""Exercise the real server's transport limits over loopback HTTP."""

import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from generateapikey.keystore import KeyStore

from swift_read_only_mcp.__main__ import MAX_TRANSPORT_CONCURRENCY
from swift_read_only_mcp.gate import MAX_CONCURRENT_REQUESTS


@contextmanager
def live_server(tmp_path, swift_port=1):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = str(store.create("workshop-one", prefix="smcp", min_entropy=128).key)

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "SWIFT_MCP_PORT": str(port),
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": f"http://127.0.0.1:{swift_port}/v1/AUTH_test",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "swift_read_only_mcp"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Test server did not start") from None
                time.sleep(0.01)

        yield port, key
    finally:
        server.terminate()
        server.communicate(timeout=10)


def request(method, name=None):
    params = {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    }
    if name is not None:
        params.update({"name": name, "arguments": {}})
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def headers(key, method, name=None):
    result = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
        "X-API-Key": key,
    }
    if name is not None:
        result["Mcp-Name"] = name
    return result


def test_idle_keep_alive_connections_do_not_exhaust_request_limit(tmp_path):
    with live_server(tmp_path) as (port, key), ExitStack() as stack:
        clients = [
            stack.enter_context(httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False))
            for _ in range(MAX_CONCURRENT_REQUESTS)
        ]
        statuses = [
            client.post(
                "/mcp", json=request("tools/list"), headers=headers(key, "tools/list")
            ).status_code
            for client in clients
        ]
        denied = stack.enter_context(
            httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False)
        ).post("/mcp", json=request("tools/list"))

    assert statuses == [200] * MAX_CONCURRENT_REQUESTS
    assert denied.status_code == 401


def test_transport_limit_still_rejects_excess_connections(tmp_path):
    with live_server(tmp_path) as (port, key), ExitStack() as stack:
        clients = [
            stack.enter_context(httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False))
            for _ in range(MAX_TRANSPORT_CONCURRENCY)
        ]
        statuses = [
            client.post(
                "/mcp", json=request("tools/list"), headers=headers(key, "tools/list")
            ).status_code
            for client in clients
        ]

    assert statuses == [200] * (MAX_TRANSPORT_CONCURRENCY - 1) + [503]


def test_active_requests_remain_bounded_at_the_public_http_boundary(tmp_path):
    release = threading.Event()
    all_started = threading.Event()
    lock = threading.Lock()
    started = 0

    class SlowSwift(BaseHTTPRequestHandler):
        def do_HEAD(self):
            nonlocal started
            with lock:
                started += 1
                if started == MAX_CONCURRENT_REQUESTS:
                    all_started.set()
            release.wait(timeout=10)
            self.send_response(204)
            self.send_header("X-Account-Container-Count", "1")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    swift = ThreadingHTTPServer(("127.0.0.1", 0), SlowSwift)
    worker = threading.Thread(target=swift.serve_forever, daemon=True)
    worker.start()
    try:
        with live_server(tmp_path, swift.server_port) as (port, key), ExitStack() as stack:
            # Keep completed requests' connections open while eight calls run.
            for _ in range(MAX_CONCURRENT_REQUESTS - 1):
                client = stack.enter_context(
                    httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False)
                )
                response = client.post(
                    "/mcp", json=request("tools/list"), headers=headers(key, "tools/list")
                )
                assert response.status_code == 200

            def invoke():
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                    return client.post(
                        "/mcp",
                        json=request("tools/call", "get_account_stats"),
                        headers=headers(key, "tools/call", "get_account_stats"),
                        timeout=15,
                    )

            with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as pool:
                futures = [pool.submit(invoke) for _ in range(MAX_CONCURRENT_REQUESTS)]
                try:
                    assert all_started.wait(timeout=5), f"Only {started} Swift requests started"
                    excess = invoke()
                    assert excess.status_code == 503
                    assert excess.json() == {"error": "Server busy"}
                finally:
                    release.set()
                responses = [future.result() for future in futures]
                assert all(response.status_code == 200 for response in responses)
                assert all(
                    response.json()["result"]["structuredContent"]["container_count"] == 1
                    for response in responses
                )
    finally:
        release.set()
        swift.shutdown()
        swift.server_close()
        worker.join(timeout=2)
