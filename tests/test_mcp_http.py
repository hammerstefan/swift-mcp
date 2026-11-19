"""Public MCP-over-HTTP boundary, not storage adapters or tool internals."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from generateapikey.keystore import KeyStore
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from swift_read_only_mcp.server import create_app


def test_missing_caller_key_cannot_list_tools(tmp_path, monkeypatch):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        store.create("workshop-one", prefix="smcp", min_entropy=128)

    monkeypatch.setenv("SWIFT_MCP_KEY_DB", str(database))
    monkeypatch.setenv("SWIFT_MCP_KEY_PEPPER", "test-pepper")
    monkeypatch.setenv("OS_AUTH_TOKEN", "fixture-swift-token")
    monkeypatch.setenv("OS_STORAGE_URL", "http://127.0.0.1:8080/v1/AUTH_test")

    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        response = client.post(
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
            },
        )

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized"}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_valid_caller_key_discovers_exactly_the_read_only_tools(tmp_path, monkeypatch):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key

    monkeypatch.setenv("SWIFT_MCP_KEY_DB", str(database))
    monkeypatch.setenv("SWIFT_MCP_KEY_PEPPER", "test-pepper")
    monkeypatch.setenv("OS_AUTH_TOKEN", "fixture-swift-token")
    monkeypatch.setenv("OS_STORAGE_URL", "http://127.0.0.1:8080/v1/AUTH_test")

    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        response = client.post(
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
                "X-API-Key": str(key),
                "Mcp-Method": "tools/list",
            },
        )

    assert response.status_code == 200, response.text
    assert sorted(tool["name"] for tool in response.json()["result"]["tools"]) == [
        "get_account_stats",
        "get_container_stats",
        "get_object_stats",
        "list_containers",
        "list_objects",
        "read_object",
    ]
    assert response.headers.get_list("cache-control") == ["no-store"]


def test_authenticated_legacy_2025_initialize_and_tools_list(tmp_path):
    operations = []

    class SwiftFixture(BaseHTTPRequestHandler):
        def do_HEAD(self):
            operations.append(self.command)
            self.send_response(204)
            self.send_header("X-Account-Container-Count", "3")
            self.send_header("X-Account-Object-Count", "12")
            self.send_header("X-Account-Bytes-Used", "240")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    swift = ThreadingHTTPServer(("127.0.0.1", 0), SwiftFixture)
    worker = Thread(target=swift.serve_forever, daemon=True)
    worker.start()
    try:
        database = tmp_path / "workshops.db"
        with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
            key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
        app = create_app(
            {
                "SWIFT_MCP_KEY_DB": str(database),
                "SWIFT_MCP_KEY_PEPPER": "test-pepper",
                "OS_AUTH_TOKEN": "fixture-swift-token",
                "OS_STORAGE_URL": f"http://127.0.0.1:{swift.server_port}/v1/AUTH_test",
            }
        )
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "fixture", "version": "1"},
            },
        }
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            denied = client.post("/mcp", json=initialize, headers=headers)
            initialized = client.post(
                "/mcp", json=initialize, headers={**headers, "X-API-Key": str(key)}
            )
            listing = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers={
                    **headers,
                    "MCP-Protocol-Version": "2025-11-25",
                    "Mcp-Method": "tools/list",
                    "X-API-Key": str(key),
                },
            )
            account = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "get_account_stats", "arguments": {}},
                },
                headers={
                    **headers,
                    "MCP-Protocol-Version": "2025-11-25",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "get_account_stats",
                    "X-API-Key": str(key),
                },
            )

        assert denied.status_code == 401
        assert initialized.status_code == 200
        assert '"protocolVersion":"2025-11-25"' in initialized.text
        assert listing.status_code == 200
        assert '"name":"read_object"' in listing.text
        assert account.status_code == 200, account.text
        assert '"container_count":3' in account.text
        assert operations == ["HEAD"]
    finally:
        swift.shutdown()
        swift.server_close()
        worker.join(timeout=2)


def test_complete_swift_credential_modes_cannot_be_combined(tmp_path, monkeypatch):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        store.create("workshop-one", prefix="smcp", min_entropy=128)

    for name, value in {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
        "ST_AUTH_VERSION": "3",
        "OS_AUTH_URL": "https://identity.example/v3",
        "OS_USERNAME": "swift-user",
        "OS_PASSWORD": "private-password",
        "OS_USER_DOMAIN_NAME": "Default",
        "OS_PROJECT_NAME": "swift-project",
        "OS_PROJECT_DOMAIN_NAME": "Default",
    }.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="Swift authentication"):
        create_app()


@pytest.mark.parametrize(
    ("variable", "url"),
    [
        ("OS_AUTH_URL", "http://identity.example/v3"),
        ("OS_STORAGE_URL", "http://storage.example/v1/account"),
        ("OS_STORAGE_URL", "https://user:password@storage.example/v1/account"),
        ("OS_STORAGE_URL", "https://storage.example/v1/account?token=secret"),
    ],
)
def test_password_with_storage_override_rejects_unsafe_urls_at_startup(tmp_path, variable, url):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        store.create("workshop-one", prefix="smcp", min_entropy=128)
    env = {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "OS_AUTH_URL": "https://identity.example/v3",
        "OS_USERNAME": "swift-user",
        "OS_PASSWORD": "private-password",
        "OS_USER_DOMAIN_NAME": "Default",
        "OS_PROJECT_NAME": "swift-project",
        "OS_PROJECT_DOMAIN_NAME": "Default",
        "OS_IDENTITY_API_VERSION": "3",
        "OS_STORAGE_URL": "https://storage.example/v1/account",
        variable: url,
    }

    with pytest.raises(ValueError, match="Invalid Swift endpoint URL"):
        create_app(env)


@pytest.mark.parametrize("credential", ["OS_AUTH_TOKEN", "OS_STORAGE_URL"])
def test_incomplete_preauthentication_is_rejected_at_startup(tmp_path, credential):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        store.create("workshop-one", prefix="smcp", min_entropy=128)

    env = {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        credential: (
            "fixture-swift-token"
            if credential == "OS_AUTH_TOKEN"
            else "https://storage.example/v1/account"
        ),
    }
    with pytest.raises(ValueError, match="Swift authentication"):
        create_app(env)


def test_revoking_one_workshop_key_does_not_revoke_another(tmp_path, monkeypatch):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        withdrawn = store.create("workshop-one", prefix="smcp", min_entropy=128)
        retained = store.create("workshop-two", prefix="smcp", min_entropy=128)

        monkeypatch.setenv("SWIFT_MCP_KEY_DB", str(database))
        monkeypatch.setenv("SWIFT_MCP_KEY_PEPPER", "test-pepper")
        monkeypatch.setenv("OS_AUTH_TOKEN", "fixture-swift-token")
        monkeypatch.setenv("OS_STORAGE_URL", "http://127.0.0.1:8080/v1/AUTH_test")
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        }
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/list",
        }

        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            assert (
                client.post(
                    "/mcp", json=request, headers={**headers, "X-API-Key": str(withdrawn.key)}
                ).status_code
                == 200
            )
            store.revoke(withdrawn.id)
            assert (
                client.post(
                    "/mcp", json=request, headers={**headers, "X-API-Key": str(withdrawn.key)}
                ).status_code
                == 401
            )
            assert (
                client.post(
                    "/mcp", json=request, headers={**headers, "X-API-Key": str(retained.key)}
                ).status_code
                == 200
            )


def test_caller_rate_limit_rejects_excess_requests(tmp_path, monkeypatch):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    for name, value in {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "SWIFT_MCP_RATE_LIMIT": "2",
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
    }.items():
        monkeypatch.setenv(name, value)

    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        statuses = [
            client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": i,
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
                    "X-API-Key": str(key),
                },
            ).status_code
            for i in range(3)
        ]

    assert statuses == [200, 200, 429]


def test_complete_keystone_v3_password_mode_starts(tmp_path, monkeypatch):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    for name, value in {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "ST_AUTH_VERSION": "3",
        "OS_AUTH_URL": "https://identity.example/v3",
        "OS_USERNAME": "swift-user",
        "OS_PASSWORD": "private-password",
        "OS_USER_DOMAIN_NAME": "Default",
        "OS_PROJECT_NAME": "swift-project",
        "OS_PROJECT_DOMAIN_NAME": "Default",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("OS_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("OS_STORAGE_URL", raising=False)

    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        response = client.post(
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
                "X-API-Key": str(key),
            },
        )

    assert response.status_code == 200
    assert '"name":"get_account_stats"' in response.text


def test_untrusted_host_is_rejected_even_with_a_valid_key(tmp_path, monkeypatch):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    for name, value in {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
    }.items():
        monkeypatch.setenv(name, value)

    with TestClient(create_app(), base_url="http://outside.example:8000") as client:
        response = client.post(
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
                "X-API-Key": str(key),
            },
        )

    assert response.status_code in (400, 403, 421)


def test_invalid_tls_opt_in_is_rejected_at_startup(tmp_path, monkeypatch):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        store.create("workshop-one", prefix="smcp", min_entropy=128)
    for name, value in {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
        "SWIFTCLIENT_INSECURE": "typo",
    }.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="TLS"):
        create_app()


@pytest.mark.parametrize(
    "allowlist", ["null", "false", '"not a list"', "[null]", '[""]', '{"name":"one"}', "not-json"]
)
def test_invalid_container_allowlist_fails_closed(tmp_path, allowlist):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        store.create("workshop-one", prefix="smcp", min_entropy=128)

    environment = {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "SWIFT_MCP_CONTAINERS": allowlist,
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
    }

    with pytest.raises(ValueError, match="Invalid container allowlist"):
        create_app(environment)


def test_explicit_insecure_swift_tls_setting_emits_warning(tmp_path, caplog):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    environment = {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": "https://swift.example/v1/AUTH_test",
        "SWIFTCLIENT_INSECURE": "true",
    }

    with caplog.at_level("WARNING", logger="swift_read_only_mcp.server"):
        app = create_app(environment)
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            response = client.post(
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
                    "X-API-Key": str(key),
                },
            )

    assert response.status_code == 200
    assert "certificate verification is disabled" in caplog.text


@pytest.mark.parametrize(
    ("setting", "value", "error"),
    [
        ("SWIFT_MCP_PAGE_LIMIT", "0", "Invalid page limit"),
        ("SWIFT_MCP_BYTE_LIMIT", "-1", "Invalid object byte limit"),
        ("SWIFT_MCP_LISTING_BYTE_LIMIT", "0", "Invalid listing byte limit"),
        ("SWIFT_MCP_RATE_LIMIT", "0", "Invalid caller rate limit"),
        ("SWIFT_MCP_PORT", "65536", "Invalid MCP port"),
    ],
)
def test_nonpositive_or_invalid_runtime_limit_fails_startup(tmp_path, setting, value, error):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        store.create("workshop-one", prefix="smcp", min_entropy=128)
    environment = {
        "SWIFT_MCP_KEY_DB": str(database),
        "SWIFT_MCP_KEY_PEPPER": "test-pepper",
        "OS_AUTH_TOKEN": "fixture-swift-token",
        "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
        setting: value,
    }

    with pytest.raises(ValueError, match=error):
        create_app(environment)


def test_nondefault_loopback_origin_is_allowed(tmp_path):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "SWIFT_MCP_PORT": "8765",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
        }
    )

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.post(
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
                "Origin": "http://127.0.0.1:8765",
                "X-API-Key": str(key),
            },
        )

    assert response.status_code == 200, response.text
    assert response.headers.get_list("cache-control") == ["no-store"]


def test_authentication_store_failure_denies_request_safely(tmp_path):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
        }
    )
    app.store.close()

    with TestClient(app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False) as client:
        response = client.post(
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
                "X-API-Key": str(key),
            },
        )

    assert response.status_code == 503
    assert response.json() == {"error": "Authentication service unavailable"}
    assert str(key) not in response.text


def test_resource_reads_and_alternate_http_credentials_require_header_key(tmp_path):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
        }
    )
    resource_read = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "resources/read",
        "params": {
            "uri": "swift://object/allowed/report.txt",
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "resources/read",
    }

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        resource = client.post("/mcp", json=resource_read, headers=headers)
        bearer = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**headers, "Authorization": f"Bearer {key}"},
        )
        query = client.post(
            f"/mcp?api_key={key}",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=headers,
        )
        get = client.get("/mcp", headers={"Accept": "text/event-stream"})
        delete = client.delete("/mcp")

    assert [resource.status_code, bearer.status_code, query.status_code] == [401, 401, 401]
    assert get.status_code == 401
    assert delete.status_code == 401


def test_each_storage_tool_and_resource_requires_authentication(tmp_path):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        revoked = store.create("workshop-one", prefix="smcp", min_entropy=128)
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
        }
    )
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        assert store.revoke(revoked.id)
    tools = (
        ("get_account_stats", {}),
        ("list_containers", {"limit": 1}),
        ("get_container_stats", {"container": "visible"}),
        ("list_objects", {"container": "visible", "limit": 1}),
        ("get_object_stats", {"container": "visible", "object_name": "object.txt"}),
        (
            "read_object",
            {"container": "visible", "object_name": "object.txt", "offset": 0, "length": 1},
        ),
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        for protocol_version in ("2025-11-25", "2026-07-28"):
            common_headers = {
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": protocol_version,
            }
            for request_id, (name, arguments) in enumerate(tools, start=1):
                params: dict[str, object] = {"name": name, "arguments": arguments}
                if protocol_version == "2026-07-28":
                    params["_meta"] = {
                        "io.modelcontextprotocol/protocolVersion": protocol_version,
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                request = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": params,
                }
                for candidate in (None, "smcp-invalid", str(revoked.key)):
                    headers = {**common_headers, "Mcp-Method": "tools/call", "Mcp-Name": name}
                    if candidate is not None:
                        headers["X-API-Key"] = candidate
                    response = client.post("/mcp", json=request, headers=headers)
                    assert response.status_code == 401, (protocol_version, name, candidate)

            resource_params: dict[str, object] = {"uri": "swift://object/visible/object.txt"}
            if protocol_version == "2026-07-28":
                resource_params["_meta"] = {
                    "io.modelcontextprotocol/protocolVersion": protocol_version,
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            resource_request = {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "resources/read",
                "params": resource_params,
            }
            for candidate in (None, "smcp-invalid", str(revoked.key)):
                headers = {
                    **common_headers,
                    "Mcp-Method": "resources/read",
                    "Mcp-Name": "swift://object/visible/object.txt",
                }
                if candidate is not None:
                    headers["X-API-Key"] = candidate
                resource = client.post("/mcp", json=resource_request, headers=headers)
                assert resource.status_code == 401, (protocol_version, candidate)


def test_websocket_transport_is_closed_without_http_response(tmp_path):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        store.create("workshop-one", prefix="smcp", min_entropy=128)
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
        }
    )

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        with pytest.raises(WebSocketDisconnect) as disconnected:
            with client.websocket_connect("/mcp"):
                pass

    assert disconnected.value.code == 1008


def test_authenticated_mcp_request_body_has_a_finite_limit(tmp_path):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
        }
    )

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        body = b"x" * (4 * 1024 * 1024 + 1)
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        unauthenticated = client.post("/mcp", content=body, headers=headers)
        response = client.post(
            "/mcp",
            content=body,
            headers={**headers, "X-API-Key": str(key)},
        )

    assert response.status_code == 413
    assert response.json() == {"error": "Request body too large"}
    assert unauthenticated.status_code == 413
    assert unauthenticated.json() == {"error": "Request body too large"}


def test_chunked_authenticated_request_body_has_a_finite_limit(tmp_path):
    database = tmp_path / "workshops.db"
    with KeyStore(str(database), pepper="test-pepper", touch=False) as store:
        key = store.create("workshop-one", prefix="smcp", min_entropy=128).key
    app = create_app(
        {
            "SWIFT_MCP_KEY_DB": str(database),
            "SWIFT_MCP_KEY_PEPPER": "test-pepper",
            "OS_AUTH_TOKEN": "fixture-swift-token",
            "OS_STORAGE_URL": "http://127.0.0.1:8080/v1/AUTH_test",
        }
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/mcp",
            content=iter([b"x" * (4 * 1024 * 1024), b"x"]),
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "X-API-Key": str(key),
            },
        )

    assert response.status_code == 413
    assert response.json() == {"error": "Request body too large"}
