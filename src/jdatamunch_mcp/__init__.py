"""jdatamunch-mcp: Token-efficient MCP server for tabular data retrieval."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("jdatamunch-mcp")
except PackageNotFoundError:
    __version__ = "unknown"
