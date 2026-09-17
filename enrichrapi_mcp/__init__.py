"""enrichrapi-mcp — MCP server for Enrichr.

This is a thin packaging layer over the canonical ``mcp_server.py`` that
lives at the repo root, so the same code path is used in development and
when installed from PyPI as ``enrichrapi-mcp``.

Run with:
    uvx enrichrapi-mcp
or
    pipx install enrichrapi-mcp && enrichrapi-mcp

Configure your Claude Desktop ``claude_desktop_config.json`` like:

    {
      "mcpServers": {
        "enrichr": {
          "command": "uvx",
          "args": ["enrichrapi-mcp"],
          "env": {"ENRICHR_API_KEY": "enr_your_key"}
        }
      }
    }
"""
from .server import main, mcp

__all__ = ["main", "mcp"]
__version__ = "0.2.0"
