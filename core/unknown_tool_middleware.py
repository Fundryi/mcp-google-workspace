"""
Middleware that reports an unknown tool as a JSON-RPC protocol error.
"""

from fastmcp.exceptions import McpError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.types import INVALID_PARAMS


class UnknownToolMiddleware(Middleware):
    """Reject a tools/call for a tool this server does not expose with -32602.

    The MCP spec (2026-07-28, Tools > Error Handling) lists an unknown tool as
    a protocol error. FastMCP 4 returns it as a tool result with isError
    instead, which tells the caller the tool ran and failed.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext):
        name = context.message.name
        if (
            context.fastmcp_context
            and await context.fastmcp_context.fastmcp.get_tool(name) is None
        ):
            raise McpError(code=INVALID_PARAMS, message=f"Unknown tool: {name}")
        return await call_next(context)
