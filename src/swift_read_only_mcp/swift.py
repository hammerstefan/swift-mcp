"""Read-only access to one configured Swift identity."""

import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import Literal, cast
from urllib.parse import quote, urlsplit

import httpx
import requests
from keystoneclient import session
from keystoneclient.auth.identity import v3

from swift_read_only_mcp.config import SwiftSettings, safe_swift_endpoint_url

SWIFT_TIMEOUT_SECONDS = 5


class SwiftBodyTooLarge(ValueError):
    """The storage response exceeded a configured byte limit."""


def read_bounded_body(response: httpx.Response, limit: int) -> bytes:
    body = bytearray()
    for chunk in response.iter_raw(chunk_size=min(65536, limit + 1)):
        if len(body) + len(chunk) > limit:
            raise SwiftBodyTooLarge("Swift response is too large")
        body.extend(chunk)
    return bytes(body)


def _reject_auth_redirect(response: requests.Response, **_: object) -> None:
    # Keystone's session does not raise on 3xx, even with redirects disabled.
    if 300 <= response.status_code < 400:
        raise ValueError("Keystone authentication redirected")


def _storage_path(container: str | None, object_name: str | None = None) -> str:
    if container is None:
        return ""
    path = "/" + quote(container, safe="")
    return path if object_name is None else path + "/" + quote(object_name, safe="/")


class SwiftBackend:
    """Only send GET and HEAD requests to validated Swift storage endpoints."""

    def __init__(self, settings: SwiftSettings, listing_byte_limit: int):
        self.settings = settings
        self.listing_byte_limit = listing_byte_limit

    def _credentials(self) -> tuple[str, str]:
        s = self.settings
        if s.token is not None:
            if not s.storage_url:
                raise ValueError("Swift storage is not configured")
            return s.storage_url, s.token

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
        with requests.Session() as client:
            client.trust_env = False
            client.hooks["response"].append(_reject_auth_redirect)
            auth_session = session.Session(
                auth=auth,
                session=client,
                verify=False if s.insecure else s.cacert or True,
                cert=client_cert,
                timeout=SWIFT_TIMEOUT_SECONDS,
                redirect=False,
            )
            access = auth.get_access(auth_session)
        storage_url = s.storage_url or access.service_catalog.url_for(
            service_type=s.keystone_options.get("service_type") or "object-store",
            endpoint_type=s.keystone_options.get("endpoint_type") or "public",
            region_name=s.keystone_options.get("region_name"),
        )
        if not isinstance(storage_url, str) or not access.auth_token:
            raise ValueError("Swift authentication did not return storage credentials")
        return storage_url, access.auth_token

    @contextmanager
    def _request(
        self,
        method: Literal["GET", "HEAD"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        byte_range: tuple[int, int] | None = None,
    ) -> Generator[httpx.Response, None, None]:
        if method not in ("GET", "HEAD"):
            raise ValueError("Swift storage method is not read-only")
        base_url, auth_token = self._credentials()
        if not safe_swift_endpoint_url(base_url):
            raise ValueError("Swift endpoint is not secure")
        headers = {"X-Auth-Token": auth_token, "Accept-Encoding": "identity"}
        if params is not None:
            headers["Accept"] = "application/json"
        if byte_range is not None:
            start, end = byte_range
            headers["Range"] = f"bytes={start}-{end}"
        with httpx.Client(
            timeout=float(SWIFT_TIMEOUT_SECONDS),
            verify=self.settings.tls_context,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with client.stream(
                method, base_url.rstrip("/") + path, params=params, headers=headers
            ) as response:
                response.raise_for_status()
                yield response

    def head(self, container: str | None = None, object_name: str | None = None) -> httpx.Headers:
        with self._request("HEAD", _storage_path(container, object_name)) as response:
            return response.headers

    @contextmanager
    def get_object(
        self, container: str, object_name: str, *, byte_range: tuple[int, int] | None = None
    ) -> Generator[httpx.Response, None, None]:
        with self._request(
            "GET", _storage_path(container, object_name), byte_range=byte_range
        ) as response:
            yield response

    def list(self, container: str | None, params: dict[str, str]) -> list[dict[str, object]]:
        """GET one JSON listing page, refusing bodies above the listing byte limit."""

        with self._request("GET", _storage_path(container), params=params) as response:
            if response.status_code == 204:
                return []
            content_encoding = response.headers.get("content-encoding", "identity").lower()
            if content_encoding not in ("", "identity"):
                raise ValueError("Swift listing response encoding is unsupported")
            content_length = response.headers.get("content-length")
            if content_length is not None and int(content_length) > self.listing_byte_limit:
                raise SwiftBodyTooLarge("Swift listing response is too large")
            body = read_bounded_body(response, self.listing_byte_limit)
        parsed: object = json.loads(body.decode("utf-8", errors="strict"))
        if not isinstance(parsed, list) or any(not isinstance(row, dict) for row in parsed):
            raise ValueError("Swift listing response has an invalid shape")
        return cast(list[dict[str, object]], parsed)
