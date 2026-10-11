"""Start Hylde-appen, eller MCP-serveren alene."""

from __future__ import annotations

import sys

from hylde.log import configure_logging


def main() -> None:
    args = sys.argv[1:]
    if args == ["--mcp"]:
        main_mcp()
        return
    if len(args) == 2 and args[0] == "--mcp-http":
        _run_http(args[1])
        return
    if args:
        print(
            "Brug: hylde    eller    hylde --mcp    eller    hylde --mcp-http PORT",
            file=sys.stderr,
        )
        raise SystemExit(2)
    from hylde.panel import run_panel

    run_panel()


def main_mcp() -> None:
    configure_logging("INFO")
    from hylde.server import mcp

    mcp.run(transport="stdio")


def _run_http(raw_port: str) -> None:
    try:
        port = int(raw_port)
    except ValueError:
        print("Porten skal være et tal.", file=sys.stderr)
        raise SystemExit(2) from None
    if not 1024 <= port <= 65535:
        print("Porten skal ligge mellem 1024 og 65535.", file=sys.stderr)
        raise SystemExit(2)
    configure_logging("INFO")
    from hylde.server import mcp

    mcp.run(transport="streamable-http", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
