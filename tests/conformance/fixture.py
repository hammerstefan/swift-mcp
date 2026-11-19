"""Credential-free, loopback-only MCP protocol fixture. Never import in production."""

import json

import uvicorn
from fastmcp import Context, FastMCP
from mcp.shared.exceptions import MCPError
from mcp.types import (
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    ClientCapabilities,
    CreateMessageRequest,
    CreateMessageRequestParams,
    InputRequiredResult,
    MissingRequiredClientCapabilityErrorData,
    SamplingCapability,
    SamplingMessage,
    TextContent,
)


def create_app():
    mcp = FastMCP("Swift MCP protocol fixture")

    @mcp.tool
    def test_simple_text() -> str:
        """Return the sample text required by the conformance runner."""

        return "This is a simple text response for testing."

    @mcp.tool
    def test_missing_capability(ctx: Context) -> InputRequiredResult:
        """Require client sampling support before returning a sampling request."""

        capabilities = ctx.session.client_capabilities
        if capabilities is None or capabilities.sampling is None:
            data = MissingRequiredClientCapabilityErrorData(
                required_capabilities=ClientCapabilities(sampling=SamplingCapability())
            )
            raise MCPError(
                code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                message="Client did not declare the required sampling capability",
                data=data.model_dump(by_alias=True, mode="json", exclude_none=True),
            )

        return InputRequiredResult(
            result_type="input_required",
            input_requests={
                "sample": CreateMessageRequest(
                    method="sampling/createMessage",
                    params=CreateMessageRequestParams(
                        messages=[
                            SamplingMessage(
                                role="user", content=TextContent(type="text", text="fixture")
                            )
                        ],
                        max_tokens=8,
                    ),
                )
            },
        )

    @mcp.resource("test://template/{id}/data", mime_type="application/json")
    def template_data(id: str) -> str:
        """Return a sample templated resource for protocol verification."""

        return json.dumps({"id": id, "templateTest": True, "data": f"Data for ID: {id}"})

    return mcp.http_app(
        transport="streamable-http",
        stateless_http=True,
        host_origin_protection=True,
        allowed_hosts=["127.0.0.1", "localhost"],
        allowed_origins=["http://127.0.0.1:8765", "http://localhost:8765"],
    )


if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=8765, access_log=False, proxy_headers=False)
