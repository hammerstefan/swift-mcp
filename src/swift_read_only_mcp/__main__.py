"""Run the authenticated host-local Swift MCP service."""

import sys

import uvicorn

from swift_read_only_mcp.gate import MAX_CONCURRENT_REQUESTS
from swift_read_only_mcp.server import create_app

# Uvicorn counts idle keep-alive connections as well as running requests.
# Leave room for those connections while retaining a finite transport limit;
# the gate independently caps active requests, including unauthenticated ones.
MAX_TRANSPORT_CONCURRENCY = MAX_CONCURRENT_REQUESTS * 4


def main() -> None:
    """Listen only on loopback, after checking caller and Swift configuration."""

    try:
        app = create_app()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=app.port,
        proxy_headers=False,
        access_log=False,
        server_header=False,
        limit_concurrency=MAX_TRANSPORT_CONCURRENCY,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
