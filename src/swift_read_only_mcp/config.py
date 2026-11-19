"""Validated startup settings for the Swift MCP service."""

import json
import logging
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from swift_read_only_mcp.openrc import load_openrc

CONTAINER_NAME_MAX_BYTES = 256
OBJECT_NAME_MAX_BYTES = 1024

_PASSWORD_FIELDS = (
    "OS_AUTH_URL",
    "OS_USERNAME",
    "OS_USER_ID",
    "OS_PASSWORD",
    "OS_PROJECT_NAME",
    "OS_PROJECT_ID",
    "OS_USER_DOMAIN_NAME",
    "OS_USER_DOMAIN_ID",
    "OS_PROJECT_DOMAIN_NAME",
    "OS_PROJECT_DOMAIN_ID",
)
_KEYSTONE_OPTIONS = (
    "user_id",
    "user_domain_id",
    "user_domain_name",
    "project_id",
    "project_name",
    "project_domain_id",
    "project_domain_name",
    "region_name",
    "service_type",
    "endpoint_type",
)
_TRUE_SETTINGS = ("true", "1", "yes", "on", "t", "y")
_FALSE_SETTINGS = ("false", "0", "no", "off", "f", "n")
_SWIFT_AUTH_FIELDS = frozenset(
    (
        *_PASSWORD_FIELDS,
        "OS_AUTH_TOKEN",
        "OS_STORAGE_URL",
        "OS_TENANT_NAME",
        "OS_INTERFACE",
        "OS_AUTH_VERSION",
        "OS_IDENTITY_API_VERSION",
        "ST_AUTH_VERSION",
        *("OS_" + option.upper() for option in _KEYSTONE_OPTIONS),
    )
)
_SWIFT_TLS_FIELDS = frozenset(("OS_CACERT", "OS_CERT", "OS_KEY"))


def bounded_text(value: object, byte_limit: int) -> bool:
    """Accept a nonempty string without NUL whose UTF-8 form fits the limit."""

    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    try:
        return len(value.encode("utf-8")) <= byte_limit
    except UnicodeEncodeError:
        return False


def valid_container_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and bounded_text(value, CONTAINER_NAME_MAX_BYTES)
        and "/" not in value
        and value not in (".", "..")
    )


def valid_object_name(value: str) -> bool:
    # Swift names may contain slashes, but dot segments can change the HTTP path.
    return bounded_text(value, OBJECT_NAME_MAX_BYTES) and not any(
        part in (".", "..") for part in value.split("/")
    )


def safe_swift_endpoint_url(url: str) -> bool:
    """Never send a Swift credential over non-loopback plaintext HTTP."""

    try:
        parsed = urlsplit(url)
        return bool(
            parsed.hostname
            and parsed.scheme in ("http", "https")
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and (parsed.scheme == "https" or parsed.hostname in ("127.0.0.1", "localhost"))
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class CallerAuthSettings:
    database: str
    pepper: str


@dataclass(frozen=True)
class SwiftSettings:
    """One validated Swift credential mode and its TLS options."""

    token: str | None
    storage_url: str | None
    auth_url: str | None
    user_name: str | None
    password: str | None
    keystone_options: Mapping[str, str]
    insecure: bool
    cacert: str | None
    cert: str | None
    cert_key: str | None
    tls_context: ssl.SSLContext


@dataclass(frozen=True)
class Limits:
    page: int
    object_bytes: int
    listing_bytes: int
    rate: int


@dataclass(frozen=True)
class Settings:
    caller_auth: CallerAuthSettings
    swift: SwiftSettings
    allowed_containers: frozenset[str] | None
    limits: Limits
    port: int


def load_settings(env: Mapping[str, str]) -> Settings:
    """Validate every setting before any resource is opened; raise ValueError on error."""

    swift_env = env
    if "SWIFT_MCP_OPENRC" in env:
        rc_values = load_openrc(env["SWIFT_MCP_OPENRC"], _SWIFT_AUTH_FIELDS | _SWIFT_TLS_FIELDS)
        swift_env = {key: value for key, value in env.items() if key not in _SWIFT_AUTH_FIELDS}
        swift_env.update(rc_values)

    return Settings(
        caller_auth=_caller_auth(env),
        swift=_swift(swift_env),
        allowed_containers=_allowlist(env),
        limits=Limits(
            page=_positive_int(env, "SWIFT_MCP_PAGE_LIMIT", 1000, "Invalid page limit"),
            object_bytes=_positive_int(
                env, "SWIFT_MCP_BYTE_LIMIT", 1048576, "Invalid object byte limit"
            ),
            listing_bytes=_positive_int(
                env, "SWIFT_MCP_LISTING_BYTE_LIMIT", 4194304, "Invalid listing byte limit"
            ),
            rate=_positive_int(env, "SWIFT_MCP_RATE_LIMIT", 60, "Invalid caller rate limit"),
        ),
        port=_port(env),
    )


def _caller_auth(env: Mapping[str, str]) -> CallerAuthSettings:
    database = env.get("SWIFT_MCP_KEY_DB", "")
    pepper = env.get("SWIFT_MCP_KEY_PEPPER", "")
    if not database or not pepper or not Path(database).is_file():
        raise ValueError("MCP caller authentication is not configured")
    return CallerAuthSettings(database=database, pepper=pepper)


def _swift(env: Mapping[str, str]) -> SwiftSettings:
    token = env.get("OS_AUTH_TOKEN", "")
    storage_url = env.get("OS_STORAGE_URL", "")
    has_password = any(env.get(field) for field in _PASSWORD_FIELDS)
    has_token = bool(token)
    password_complete = bool(
        env.get("OS_AUTH_URL")
        and env.get("OS_PASSWORD")
        and (
            env.get("OS_USER_ID")
            or (
                env.get("OS_USERNAME")
                and (env.get("OS_USER_DOMAIN_NAME") or env.get("OS_USER_DOMAIN_ID"))
            )
        )
        and (
            env.get("OS_PROJECT_ID")
            or (
                env.get("OS_PROJECT_NAME")
                and (env.get("OS_PROJECT_DOMAIN_NAME") or env.get("OS_PROJECT_DOMAIN_ID"))
            )
        )
        and (
            env.get("ST_AUTH_VERSION")
            or env.get("OS_AUTH_VERSION")
            or env.get("OS_IDENTITY_API_VERSION")
        )
        in ("3", "3.0")
    )
    if (has_token and (not token or not storage_url or has_password)) or (
        not has_token and not password_complete
    ):
        raise ValueError("Swift authentication is not configured")

    raw_insecure = env.get("SWIFTCLIENT_INSECURE", "false").lower()
    if raw_insecure not in (*_TRUE_SETTINGS, *_FALSE_SETTINGS):
        raise ValueError("Invalid Swift TLS verification setting")
    insecure = raw_insecure in _TRUE_SETTINGS
    if insecure:
        logging.getLogger(__name__).warning(
            "WARNING: Swift TLS certificate verification is disabled by SWIFTCLIENT_INSECURE"
        )
    cacert = env.get("OS_CACERT") or None
    cert = env.get("OS_CERT") or None
    cert_key = env.get("OS_KEY") or None
    if cert_key and not cert:
        raise ValueError("Invalid Swift client certificate settings")
    try:
        tls_context = ssl.create_default_context(cafile=cacert)
        if insecure:
            tls_context.check_hostname = False
            tls_context.verify_mode = ssl.CERT_NONE
        if cert:
            tls_context.load_cert_chain(certfile=cert, keyfile=cert_key)
    except (OSError, ssl.SSLError, ValueError):
        raise ValueError("Invalid Swift TLS configuration") from None

    if (storage_url and not safe_swift_endpoint_url(storage_url)) or (
        not has_token and not safe_swift_endpoint_url(env["OS_AUTH_URL"])
    ):
        raise ValueError("Invalid Swift endpoint URL")
    return SwiftSettings(
        token=token if has_token else None,
        storage_url=storage_url or None,
        auth_url=None if has_token else env["OS_AUTH_URL"],
        user_name=None if has_token else env.get("OS_USERNAME") or None,
        password=None if has_token else env["OS_PASSWORD"],
        keystone_options={}
        if has_token
        else {
            **{
                option: env["OS_" + option.upper()]
                for option in _KEYSTONE_OPTIONS
                if env.get("OS_" + option.upper())
            },
            **(
                {"endpoint_type": env["OS_INTERFACE"]}
                if env.get("OS_INTERFACE") and not env.get("OS_ENDPOINT_TYPE")
                else {}
            ),
        },
        insecure=insecure,
        cacert=cacert,
        cert=cert,
        cert_key=cert_key,
        tls_context=tls_context,
    )


def _allowlist(env: Mapping[str, str]) -> frozenset[str] | None:
    raw = env.get("SWIFT_MCP_CONTAINERS")
    if raw is None:
        return None
    try:
        names: object = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Invalid container allowlist") from None
    if not isinstance(names, list) or any(not valid_container_name(name) for name in names):
        raise ValueError("Invalid container allowlist")
    return frozenset(names)


def _positive_int(env: Mapping[str, str], name: str, default: int, error: str) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError:
        raise ValueError(error) from None
    if value < 1:
        raise ValueError(error)
    return value


def _port(env: Mapping[str, str]) -> int:
    try:
        port = int(env.get("SWIFT_MCP_PORT", "8000"))
    except ValueError:
        raise ValueError("Invalid MCP port") from None
    if not 1 <= port <= 65535:
        raise ValueError("Invalid MCP port")
    return port
