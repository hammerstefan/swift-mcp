"""Read-only access to one configured Swift identity."""

import json
from typing import cast
from urllib.parse import urlsplit

import httpx
from fastmcp.exceptions import ToolError
from keystoneclient import session
from keystoneclient.auth.identity import v3
from swiftclient.client import Connection

from swift_read_only_mcp.config import SwiftSettings, safe_swift_endpoint_url

SWIFT_TIMEOUT_SECONDS = 5


class SwiftBackend:
    """Open a fresh Swift connection per operation and stream bounded listings."""

    def __init__(self, settings: SwiftSettings, listing_byte_limit: int):
        self.settings = settings
        self.listing_byte_limit = listing_byte_limit

    def connection(self) -> Connection:
        s = self.settings
        options = {
            "retries": 0,
            "retry_on_ratelimit": False,
            "timeout": SWIFT_TIMEOUT_SECONDS,
            "insecure": s.insecure,
            "cacert": s.cacert,
            "cert": s.cert,
            "cert_key": s.cert_key,
        }
        if s.token is not None:
            return Connection(preauthurl=s.storage_url, preauthtoken=s.token, **options)
        if s.storage_url is not None:
            if s.auth_url is None or s.password is None:
                raise ValueError("Swift authentication is not configured")
            identity_options = {
                key: value
                for key, value in s.keystone_options.items()
                if key
                in (
                    "user_id",
                    "user_domain_id",
                    "user_domain_name",
                    "project_id",
                    "project_name",
                    "project_domain_id",
                    "project_domain_name",
                )
            }
            auth_url = s.auth_url.rstrip("/")
            if urlsplit(auth_url).path.rsplit("/", 1)[-1] not in ("v3", "v3.0"):
                auth_url += "/v3"
            auth = v3.Password(
                auth_url=auth_url, username=s.user_name, password=s.password, **identity_options
            )
            client_cert = (s.cert, s.cert_key) if s.cert and s.cert_key else s.cert
            auth_session = session.Session(
                auth=auth,
                verify=False if s.insecure else s.cacert or True,
                cert=client_cert,
                timeout=SWIFT_TIMEOUT_SECONDS,
                redirect=False,
            )
            token = (auth_session.get_auth_headers() or {}).get("X-Auth-Token")
            if not token:
                raise ValueError("Swift authentication did not return a token")
            return Connection(preauthurl=s.storage_url, preauthtoken=token, **options)
        swift_connection = Connection(
            authurl=s.auth_url,
            user=s.user_name,
            key=s.password,
            auth_version="3",
            os_options=dict(s.keystone_options),
            **options,
        )
        storage_endpoint, _ = swift_connection.get_auth()
        if not safe_swift_endpoint_url(storage_endpoint):
            raise ToolError("Swift endpoint is not secure")
        return swift_connection

    def list(self, path: str, params: dict[str, str]) -> list[dict[str, object]]:
        """GET one JSON listing page, refusing bodies above the listing byte limit."""

        base_url, auth_token = self._listing_credentials()
        if not safe_swift_endpoint_url(base_url):
            raise ValueError("Swift endpoint is not secure")
        body = bytearray()
        with httpx.Client(
            timeout=float(SWIFT_TIMEOUT_SECONDS),
            verify=self.settings.tls_context,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with client.stream(
                "GET",
                base_url.rstrip("/") + path,
                params=params,
                headers={
                    "X-Auth-Token": auth_token,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if response.status_code == 204:
                    return []
                if not 200 <= response.status_code < 300:
                    raise ValueError("Swift listing request failed")
                content_encoding = response.headers.get("content-encoding", "identity").lower()
                if content_encoding not in ("", "identity"):
                    raise ValueError("Swift listing response encoding is unsupported")
                content_length = response.headers.get("content-length")
                if content_length is not None and int(content_length) > self.listing_byte_limit:
                    raise ValueError("Swift listing response is too large")
                for chunk in response.iter_raw(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > self.listing_byte_limit:
                        raise ValueError("Swift listing response is too large")
                    body.extend(chunk)
        parsed: object = json.loads(body.decode("utf-8", errors="strict"))
        if not isinstance(parsed, list) or any(not isinstance(row, dict) for row in parsed):
            raise ValueError("Swift listing response has an invalid shape")
        return cast(list[dict[str, object]], parsed)

    def _listing_credentials(self) -> tuple[str, str]:
        s = self.settings
        if s.token is not None and s.storage_url is not None:
            return s.storage_url, s.token
        swift_connection = self.connection()
        if not swift_connection.url or not swift_connection.token:
            raise ValueError("Swift authentication did not return a storage endpoint")
        return swift_connection.url, swift_connection.token
