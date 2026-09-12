"""Source-tree compatibility entry point for the installed MCP server."""

from good_student import mcp_server as _server

mcp = _server.mcp
__all__ = list(_server.__all__)
globals().update({name: getattr(_server, name) for name in __all__ if name != "mcp"})


if __name__ == "__main__":
    mcp.run()
