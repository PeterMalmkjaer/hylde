"""Lokal app: start, stop og indstillinger. Lytter kun på 127.0.0.1."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from hylde.envfile import UserError, apply_form, public_view, runtime_settings
from hylde.health import health_report
from hylde.providers import HttpxTransport
from hylde.supervisor import Supervisor, log_tail
from hylde.table import load_catalog

HOST = "127.0.0.1"
_MAX_BODY = 32_768


class Panel:
    def __init__(
        self,
        data_dir: Path,
        *,
        ui_port: int = 8765,
        command_for_port: Any = None,
    ) -> None:
        self.data_dir = data_dir
        self.ui_port = ui_port
        self.supervisor = Supervisor(data_dir, command_for_port)
        self.httpd: PanelServer | None = None

    @property
    def env_path(self) -> Path:
        return self.data_dir / ".env"

    def bind(self) -> int:
        self.httpd = PanelServer(self, (HOST, self.ui_port))
        self.ui_port = int(self.httpd.server_address[1])
        return self.ui_port

    def serve_forever(self) -> None:
        if self.httpd is None:
            self.bind()
        assert self.httpd is not None
        self.httpd.serve_forever()

    def shutdown(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()

    def state(self) -> dict[str, object]:
        view = public_view(self.env_path)
        port = int(view["mcp_port"])
        return {
            "running": self.supervisor.running(),
            "pid": self.supervisor.pid(),
            "mcp_url": f"http://{HOST}:{port}/mcp",
            "ui_port": self.ui_port,
            "settings": view,
            "health": self._health(),
            "log": log_tail(self.data_dir / "mcp.log"),
        }

    def save(self, payload: dict[str, object]) -> dict[str, object]:
        self._reject_same_port(payload)
        view = apply_form(self.env_path, payload)
        return {"settings": view, "running": self.supervisor.running()}

    def start(self) -> None:
        view = public_view(self.env_path)
        self._reject_same_port({"mcp_port": view["mcp_port"]})
        self.supervisor.start(int(view["mcp_port"]))

    def stop(self) -> None:
        self.supervisor.stop()

    def restart(self) -> None:
        self.supervisor.stop()
        self.start()

    def _reject_same_port(self, payload: dict[str, object]) -> None:
        port = payload.get("mcp_port")
        if isinstance(port, int) and port == self.ui_port:
            raise UserError("MCP-porten må ikke være den samme som appens port.")

    def _health(self) -> str:
        try:
            settings = runtime_settings(self.data_dir)
            catalog = load_catalog(settings.table_path)
            return asyncio.run(health_report(settings, catalog, HttpxTransport()))
        except Exception as exc:  # noqa: BLE001 — vises til brugeren, uden stak
            return f"Kunne ikke tjekke sundheden: {exc.__class__.__name__}"


class PanelServer(ThreadingHTTPServer):
    def __init__(self, panel: Panel, address: tuple[str, int]) -> None:
        self.panel = panel
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: PanelServer

    def do_GET(self) -> None:  # noqa: N802
        if not self._local():
            self.close_connection = True
            self._json(403, {"error": "Hylde svarer kun på denne maskine."})
            return
        if self.path in {"/", "/index.html"}:
            body = Path(__file__).with_name("panel.html").read_bytes()
            self._bytes(200, body, "text/html; charset=utf-8")
            return
        if self.path == "/api/state":
            self._json(200, self.server.panel.state())
            return
        if self.path == "/favicon.ico":
            self._bytes(204, b"", "image/x-icon")
            return
        self._json(404, {"error": "Siden findes ikke."})

    def do_POST(self) -> None:  # noqa: N802
        if not self._local():
            self.close_connection = True
            self._json(403, {"error": "Hylde svarer kun på denne maskine."})
            return
        try:
            payload = self._body()
        except UserError as exc:
            self._json(400, {"error": str(exc)})
            return
        panel = self.server.panel
        try:
            if self.path == "/api/settings":
                self._json(200, panel.save(payload))
                return
            if self.path == "/api/start":
                panel.start()
                self._json(200, panel.state())
                return
            if self.path == "/api/stop":
                panel.stop()
                self._json(200, panel.state())
                return
            if self.path == "/api/restart":
                panel.restart()
                self._json(200, panel.state())
                return
        except UserError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(404, {"error": "Siden findes ikke."})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _local(self) -> bool:
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            return False
        origin = self.headers.get("Origin")
        allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        return origin is None or origin in allowed

    def _body(self) -> dict[str, object]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise UserError("Kunne ikke læse formularen.") from exc
        if length < 0 or length > _MAX_BODY:
            raise UserError("Formularen er for stor.")
        if length == 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise UserError("Kunne ikke læse formularen.") from exc
        if not isinstance(payload, dict):
            raise UserError("Kunne ikke læse formularen.")
        return payload

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._bytes(status, body, "application/json; charset=utf-8")

    def _bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)


def run_panel() -> None:
    from hylde.config import default_data_dir

    raw_port = os.environ.get("HYLDE_UI_PORT", "8765")
    try:
        ui_port = int(raw_port)
    except ValueError:
        print("HYLDE_UI_PORT skal være et tal.", file=sys.stderr)
        raise SystemExit(2) from None
    panel = Panel(default_data_dir(), ui_port=ui_port)
    try:
        port = panel.bind()
    except OSError:
        print(f"Port {ui_port} er optaget. Sæt HYLDE_UI_PORT til en fri port.", file=sys.stderr)
        raise SystemExit(1) from None
    url = f"http://{HOST}:{port}"
    print(f"Hylde-appen: {url}", file=sys.stderr)
    print("Luk med Ctrl+C. Serveren til Claude stopper, når du trykker Stop.", file=sys.stderr)
    thread = threading.Thread(target=panel.serve_forever, name="hylde-panel", daemon=True)
    thread.start()
    if os.environ.get("HYLDE_NO_BROWSER") != "1":
        webbrowser.open(url)
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\nStopper.", file=sys.stderr)
    finally:
        panel.supervisor.stop(only_spawned=True)
        panel.shutdown()
        thread.join(timeout=2)
