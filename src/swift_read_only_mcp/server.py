"""Fail-closed HTTP entry point for the Swift MCP service."""

import base64
import logging
import os
import re
import sqlite3
from collections.abc import Mapping

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.resources import ResourceContent, ResourceResult, ResourceSecurity
from generateapikey.keystore import KeyStore

from swift_read_only_mcp.config import (
    CONTAINER_NAME_MAX_BYTES,
    OBJECT_NAME_MAX_BYTES,
    Settings,
    bounded_text,
    load_settings,
    valid_container_name,
    valid_object_name,
)
from swift_read_only_mcp.gate import MAX_REQUEST_BODY_BYTES, CallerKeyGate
from swift_read_only_mcp.swift import SwiftBackend, SwiftBodyTooLarge, read_bounded_body

__all__ = ["MAX_REQUEST_BODY_BYTES", "CallerKeyGate", "create_app"]


def create_app(environ: Mapping[str, str] | None = None) -> CallerKeyGate:
    """Create an HTTP service only with valid settings and a nonempty caller-key store."""

    _silence_dependency_logs()
    settings = load_settings(os.environ if environ is None else environ)
    store = _open_key_store(settings)
    mcp = FastMCP("Swift Read-only")
    _register_storage_handlers(
        mcp, settings, SwiftBackend(settings.swift, settings.limits.listing_bytes)
    )
    allowed_origins = [f"http://127.0.0.1:{settings.port}", f"http://localhost:{settings.port}"]
    app = mcp.http_app(
        transport="streamable-http",
        stateless_http=True,
        host_origin_protection=True,
        allowed_hosts=["127.0.0.1", "localhost"],
        allowed_origins=allowed_origins,
    )
    return CallerKeyGate(
        app, store, settings.caller_auth.pepper, settings.limits.rate, settings.port
    )


def _silence_dependency_logs() -> None:
    # Swift/Keystone debug logs can contain credential prefixes, arbitrary
    # user metadata and object bytes. Keep them out of even DEBUG host logs.
    for logger_name in (
        "keystoneclient",
        "keystoneauth",
        "keystoneauth1",
        "httpx",
        "httpcore",
        "urllib3",
    ):
        dependency_logger = logging.getLogger(logger_name)
        if not any(
            isinstance(handler, logging.NullHandler) for handler in dependency_logger.handlers
        ):
            dependency_logger.addHandler(logging.NullHandler())
        dependency_logger.propagate = False


def _open_key_store(settings: Settings) -> KeyStore:
    store = None
    try:
        store = KeyStore(
            settings.caller_auth.database,
            pepper=settings.caller_auth.pepper,
            touch=False,
            table_check=False,
        )
        if store.list(active_only=True):
            return store
    except (sqlite3.Error, OSError, ValueError):
        pass
    if store is not None:
        store.close()
    raise ValueError("MCP caller authentication is not configured")


def _textual_content_type(content_type: str) -> bool:
    media_type = content_type.partition(";")[0].strip().lower()
    return (
        media_type.startswith("text/")
        or media_type in ("application/json", "application/xml")
        or media_type.endswith(("+json", "+xml"))
    )


def _nonnegative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _optional_text(value: str | None, byte_limit: int, error: str) -> None:
    if value is not None and not bounded_text(value, byte_limit):
        raise ToolError(error)


def _object_payload(data: bytes, content_type: str, size: int, offset: int) -> dict[str, object]:
    common = {
        "content_type": content_type,
        "size": size,
        "offset": offset,
        "partial": offset + len(data) < size,
    }
    if _textual_content_type(content_type):
        try:
            return {"encoding": "utf-8", "data": data.decode("utf-8", errors="strict"), **common}
        except UnicodeDecodeError:
            pass
    return {"encoding": "base64", "data": base64.b64encode(data).decode("ascii"), **common}


def _register_storage_handlers(mcp: FastMCP, settings: Settings, swift: SwiftBackend) -> None:
    allowed_containers = settings.allowed_containers
    page_max = settings.limits.page
    byte_max = settings.limits.object_bytes

    def check_container(container: str, error: type[Exception] = ToolError) -> None:
        if not valid_container_name(container):
            raise error("Invalid container name")
        if allowed_containers is not None and container not in allowed_containers:
            raise error("Container not accessible")

    def check_object(container: str, object_name: str, error: type[Exception] = ToolError) -> None:
        check_container(container, error)
        if not valid_object_name(object_name):
            raise error("Invalid object name")

    def check_page_size(limit: int | None) -> int:
        page_size = page_max if limit is None else limit
        if page_size < 1 or page_size > page_max:
            raise ToolError("Invalid page size")
        return page_size

    @mcp.tool
    def get_account_stats() -> dict[str, int]:
        """Read non-sensitive account statistics from Swift."""

        if allowed_containers is not None:
            raise ToolError("Container not accessible: account totals include excluded containers")
        try:
            headers = swift.head()
            return {
                name: int(headers[header])
                for name, header in (
                    ("container_count", "x-account-container-count"),
                    ("object_count", "x-account-object-count"),
                    ("bytes_used", "x-account-bytes-used"),
                )
                if header in headers
            }
        except Exception:
            raise ToolError("Swift account read failed") from None

    @mcp.tool
    def list_containers(
        limit: int | None = None, marker: str | None = None, prefix: str | None = None
    ) -> dict[str, object]:
        """List one bounded page of containers, with an optional marker and prefix."""

        page_size = check_page_size(limit)
        _optional_text(marker, CONTAINER_NAME_MAX_BYTES, "Invalid marker")
        _optional_text(prefix, CONTAINER_NAME_MAX_BYTES, "Invalid prefix")
        if allowed_containers is not None:
            # Page through the allowlist itself, so no account-wide listing is made and no
            # excluded container name can reach the caller.
            remaining = [
                name
                for name in sorted(allowed_containers)
                if (prefix is None or name.startswith(prefix)) and (marker is None or name > marker)
            ]
            page = remaining[:page_size]
            return {
                "containers": [{"name": name} for name in page],
                "next_marker": page[-1] if len(remaining) > page_size else None,
            }
        try:
            params = {"format": "json", "limit": str(page_size)}
            if marker:
                params["marker"] = marker
            if prefix:
                params["prefix"] = prefix
            raw_entries = swift.list(None, params)
            if len(raw_entries) > page_size:
                raise ToolError("Swift returned an oversized container page")
            if any(
                not valid_container_name(entry.get("name"))
                or any(
                    key in entry and not _nonnegative_integer(entry[key])
                    for key in ("count", "bytes")
                )
                for entry in raw_entries
            ):
                raise ToolError("Swift returned an invalid container listing")
            return {
                "containers": [
                    {key: entry[key] for key in ("name", "count", "bytes") if key in entry}
                    for entry in raw_entries
                ],
                "next_marker": raw_entries[-1]["name"] if len(raw_entries) == page_size else None,
            }
        except ToolError:
            raise
        except Exception:
            raise ToolError("Swift container listing failed") from None

    @mcp.tool
    def get_container_stats(container: str) -> dict[str, int]:
        """Read non-sensitive statistics for an allowed Swift container."""

        check_container(container)
        try:
            headers = swift.head(container)
            return {
                name: int(headers[header])
                for name, header in (
                    ("object_count", "x-container-object-count"),
                    ("bytes_used", "x-container-bytes-used"),
                )
                if header in headers
            }
        except Exception:
            raise ToolError("Swift container read failed") from None

    @mcp.tool
    def list_objects(
        container: str,
        limit: int | None = None,
        marker: str | None = None,
        prefix: str | None = None,
        delimiter: str | None = None,
    ) -> dict[str, object]:
        """List one bounded page of current object names with Swift-native filters."""

        check_container(container)
        page_size = check_page_size(limit)
        _optional_text(marker, OBJECT_NAME_MAX_BYTES, "Invalid marker")
        _optional_text(prefix, OBJECT_NAME_MAX_BYTES, "Invalid prefix")
        if delimiter is not None and (len(delimiter) != 1 or delimiter == "\x00"):
            raise ToolError("Invalid delimiter")
        try:
            params = {"format": "json", "limit": str(page_size)}
            if marker:
                params["marker"] = marker
            if prefix:
                params["prefix"] = prefix
            if delimiter:
                params["delimiter"] = delimiter
            raw_entries = swift.list(container, params)
            if len(raw_entries) > page_size:
                raise ToolError("Swift returned an oversized object page")
            for entry in raw_entries:
                if not _valid_object_entry(entry):
                    raise ToolError("Swift returned an invalid object listing")
            entries = [
                {
                    key: entry[key]
                    for key in ("name", "subdir", "bytes", "content_type", "last_modified", "hash")
                    if key in entry
                }
                for entry in raw_entries
            ]
            last = raw_entries[-1] if len(raw_entries) == page_size else {}
            return {"objects": entries, "next_marker": last.get("name", last.get("subdir"))}
        except ToolError:
            raise
        except Exception:
            raise ToolError("Swift object listing failed") from None

    @mcp.tool
    def get_object_stats(container: str, object_name: str) -> dict[str, object]:
        """Read reviewed metadata for the current object version."""

        check_object(container, object_name)
        try:
            headers = swift.head(container, object_name)
            result: dict[str, object] = {
                key: headers[header]
                for key, header in (
                    ("content_type", "content-type"),
                    ("etag", "etag"),
                    ("last_modified", "last-modified"),
                )
                if header in headers
            }
            if "content-length" in headers:
                result["size"] = int(headers["content-length"])
            return result
        except Exception:
            raise ToolError("Swift object metadata read failed") from None

    @mcp.tool
    def read_object(
        container: str, object_name: str, offset: int = 0, length: int | None = None
    ) -> dict[str, object]:
        """Read a bounded byte range of the current object version."""

        check_object(container, object_name)
        read_length = byte_max if length is None else length
        if offset < 0 or read_length < 1 or read_length > byte_max:
            raise ToolError("Invalid byte range")
        try:
            with swift.get_object(
                container, object_name, byte_range=(offset, offset + read_length - 1)
            ) as response:
                headers = response.headers
                content_type = headers.get("content-type", "application/octet-stream")
                if response.status_code == 200 and headers.get("content-length") == "0":
                    # Swift ignores a Range header on an empty object.
                    return _object_payload(b"", content_type, 0, offset)
                content_range = headers.get("content-range", "")
                matched = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                if response.status_code != 206 or matched is None:
                    raise ToolError("Swift did not honor the byte range")
                start, end, size = map(int, matched.groups())
                if start != offset or end < start or end >= offset + read_length or size <= end:
                    raise ToolError("Swift returned an invalid byte range")
                try:
                    data = read_bounded_body(response, end - start + 1)
                except (SwiftBodyTooLarge, httpx.RemoteProtocolError):
                    raise ToolError("Swift returned an invalid byte range") from None
                if len(data) != end - start + 1:
                    raise ToolError("Swift returned an invalid byte range")
                return _object_payload(data, content_type, size, offset)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 416:
                raise ToolError("Swift object read failed") from None
            return read_past_end(container, object_name, offset)
        except ToolError:
            raise
        except Exception:
            raise ToolError("Swift object read failed") from None

    def read_past_end(container: str, object_name: str, offset: int) -> dict[str, object]:
        try:
            head = swift.head(container, object_name)
            size = int(head["content-length"])
        except Exception:
            raise ToolError("Swift object read failed") from None
        if size < 0 or offset < size:
            raise ToolError("Swift returned an invalid byte range")
        return _object_payload(
            b"", head.get("content-type", "application/octet-stream"), size, offset
        )

    @mcp.resource(
        "swift://object/{container}/{object_name}",
        security=ResourceSecurity(exempt_params={"object_name"}),
    )
    def object_resource(container: str, object_name: str) -> ResourceResult:
        """Return a complete current object only if it fits within the byte limit."""

        check_object(container, object_name, ResourceError)
        try:
            head = swift.head(container, object_name)
            head_size = int(head["content-length"])
            if head_size > byte_max or head_size < 0:
                raise ResourceError("Object exceeds resource limit; use read_object instead")
            with swift.get_object(container, object_name) as response:
                headers = response.headers
                data = read_bounded_body(response, byte_max)
            # Without a Content-Length (chunked transfer) the HEAD size is the only
            # completeness check; a mismatch means a truncated or changed object.
            expected_size = int(headers.get("content-length", head_size))
            if expected_size != len(data):
                raise ResourceError("Swift returned an incomplete object")
            content_type = headers.get("content-type", "application/octet-stream")
            if _textual_content_type(content_type):
                try:
                    return ResourceResult(
                        contents=[
                            ResourceContent(
                                content=data.decode("utf-8", errors="strict"),
                                mime_type=content_type,
                            )
                        ]
                    )
                except UnicodeDecodeError:
                    pass
            return ResourceResult(contents=[ResourceContent(content=data, mime_type=content_type)])
        except SwiftBodyTooLarge:
            raise ResourceError("Object exceeds resource limit; use read_object instead") from None
        except ResourceError:
            raise
        except Exception:
            raise ResourceError("Swift resource read failed") from None


def _valid_object_entry(entry: dict[str, object]) -> bool:
    name = entry.get("name")
    subdir = entry.get("subdir")
    if (name is None) == (subdir is None):
        return False
    if not bounded_text(name if name is not None else subdir, OBJECT_NAME_MAX_BYTES):
        return False
    if "bytes" in entry and not _nonnegative_integer(entry["bytes"]):
        return False
    return not any(
        key in entry and not bounded_text(entry[key], 4096)
        for key in ("content_type", "last_modified", "hash")
    )
