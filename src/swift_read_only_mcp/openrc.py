"""Read the limited OpenStack RC assignment format without executing shell code."""

import os
import re
import stat
from collections.abc import Mapping, Set

_MAX_RC_BYTES = 64 * 1024
_ASSIGNMENT = re.compile(r"\s*(?:export\s+)?((?:OS_[A-Z0-9_]+|ST_AUTH_VERSION))=(.*)")
_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_OS_START = re.compile(r"\s*(?:export\s+)?(?:OS_[A-Za-z0-9_]+|ST_AUTH_VERSION)\b")


def expand_openrc_path(path: str) -> str:
    """Expand a current-user home prefix without interpreting other shell syntax."""

    return os.path.expanduser(path) if path == "~" or path.startswith("~/") else path


def load_openrc(path: str, allowed: Set[str]) -> dict[str, str]:
    """Load supported assignments from a bounded regular file; never run it as a script."""

    try:
        fd = os.open(expand_openrc_path(path), os.O_RDONLY | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Invalid Swift RC file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_RC_BYTES + 1)
        finally:
            os.close(fd)
        if len(raw) > _MAX_RC_BYTES:
            raise ValueError("Invalid Swift RC file")
        text = raw.decode("utf-8")
    except (OSError, UnicodeError, ValueError):
        raise ValueError("Invalid Swift RC file") from None

    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _ASSIGNMENT.fullmatch(line)
        if match is None:
            if _OS_START.match(line):
                raise ValueError("Invalid Swift RC assignment")
            continue
        name, raw_value = match.groups()
        if name not in allowed or name in values:
            raise ValueError("Invalid Swift RC assignment")
        values[name] = _assignment_value(raw_value, values)
    return values


def _assignment_value(raw: str, values: Mapping[str, str]) -> str:
    """Parse one shell-style word, with references only to earlier RC assignments."""

    result: list[str] = []
    byte_count = 0

    def append(part: str) -> None:
        nonlocal byte_count
        byte_count += len(part.encode("utf-8"))
        if "\x00" in part or byte_count > _MAX_RC_BYTES:
            raise ValueError("Invalid Swift RC assignment")
        result.append(part)

    quote = ""
    index = 0
    while index < len(raw):
        char = raw[index]
        if quote == "'":
            if char == "'":
                quote = ""
            else:
                append(char)
        elif char in "'\"" and (not quote or quote == char):
            quote = "" if quote else char
        elif char == "\\":
            index += 1
            if index == len(raw):
                raise ValueError("Invalid Swift RC assignment")
            if quote == '"' and raw[index] not in '$`"\\':
                append("\\")
            append(raw[index])
        elif char == "$":
            start = index + 1
            braced = start < len(raw) and raw[start] == "{"
            start += int(braced)
            match = _VARIABLE.match(raw, start)
            if match is None or (braced and raw[match.end() : match.end() + 1] != "}"):
                raise ValueError("Invalid Swift RC assignment")
            name = match.group()
            if name not in values:
                raise ValueError("Invalid Swift RC assignment")
            append(values[name])
            index = match.end() if braced else match.end() - 1
        elif char == "`" or (not quote and char in ";&|<>()"):
            raise ValueError("Invalid Swift RC assignment")
        elif not quote and char.isspace():
            remainder = raw[index:].strip()
            if remainder and not remainder.startswith("#"):
                raise ValueError("Invalid Swift RC assignment")
            break
        else:
            append(char)
        index += 1
    if quote:
        raise ValueError("Invalid Swift RC assignment")
    return "".join(result)
