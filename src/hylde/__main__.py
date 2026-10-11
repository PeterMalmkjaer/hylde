"""Start Hylde som stdio-MCP."""

from hylde.log import configure_logging
from hylde.server import mcp


def main() -> None:
    configure_logging("INFO")
    mcp.run()


if __name__ == "__main__":
    main()
