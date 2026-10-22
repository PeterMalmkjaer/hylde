"""Appen: gem nøgler, start og stop, uden at vise nøglerne."""

from __future__ import annotations

import asyncio
import http.client
import json
import socket
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from hylde.envfile import apply_form, child_env, public_view, read_values, runtime_settings
from hylde.panel import Panel


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def sleeper(_port: int) -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(30)"]


@pytest.fixture
def panel(tmp_path: Path):
    app = Panel(tmp_path, ui_port=0, command_for_port=sleeper)
    port = app.bind()
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, port
    finally:
        app.supervisor.stop()
        app.shutdown()
        thread.join(timeout=2)


def call(
    port: int,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    host: str | None = None,
):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = None
    headers = {}
    if host is not None:
        headers["Host"] = host
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, raw


def test_page_has_start_and_settings(panel) -> None:
    _app, port = panel
    status, raw = call(port, "GET", "/")
    page = raw.decode("utf-8")
    assert status == 200
    assert "Start" in page
    assert "Gem indstillinger" in page
    assert "Anthropic" in page
    assert "Ollama Cloud" in page
    assert "llama.cpp" in page
    assert 'id="ollama_model"' in page
    assert "local_kind" not in page


def test_state_hides_secrets(panel, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, port = panel
    secret = "sk-ant-shellsecretvalue123456"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    status, raw = call(port, "GET", "/api/state")
    text = raw.decode("utf-8")
    assert status == 200
    assert secret not in text
    body = json.loads(text)
    assert body["settings"]["anthropic_set"] is False
    assert body["settings"]["anthropic_in_environment"] is True
    assert body["running"] is False


def test_save_keeps_blank_and_can_clear(panel, tmp_path: Path) -> None:
    app, port = panel
    secret = "sk-ant-filesecretvalue123456"
    status, raw = call(
        port,
        "POST",
        "/api/settings",
        {
            "anthropic_api_key": secret,
            "ollama_model": "qwen2.5-coder:7b",
            "llama_enabled": True,
            "llama_model": "local-gguf",
            "dry_run": True,
            "mcp_port": 8791,
        },
    )
    assert status == 200
    assert secret not in raw.decode("utf-8")
    saved = read_values(tmp_path / ".env")
    assert saved["ANTHROPIC_API_KEY"] == secret
    assert saved["HYLDE_LOCAL_BASE_URL"] == "http://127.0.0.1:11434/v1"
    assert saved["HYLDE_LOCAL_MODEL"] == "qwen2.5-coder:7b"
    assert saved["HYLDE_LLAMA_ENABLED"] == "1"
    assert saved["HYLDE_LLAMA_BASE_URL"] == "http://127.0.0.1:8080/v1"
    assert saved["HYLDE_LLAMA_MODEL"] == "local-gguf"
    assert saved["HYLDE_DRY_RUN"] == "1"
    mode = (tmp_path / ".env").stat().st_mode
    assert mode & stat.S_IRWXG == 0
    assert mode & stat.S_IRWXO == 0

    status, _raw = call(
        port,
        "POST",
        "/api/settings",
        {"dry_run": False, "mcp_port": 8791},
    )
    assert status == 200
    assert read_values(app.env_path)["ANTHROPIC_API_KEY"] == secret

    status, raw = call(
        port,
        "POST",
        "/api/settings",
        {
            "anthropic_api_key_clear": True,
            "dry_run": False,
            "mcp_port": 8791,
        },
    )
    assert status == 200
    assert "ANTHROPIC_API_KEY" not in read_values(app.env_path)
    assert json.loads(raw)["settings"]["anthropic_set"] is False


def test_rejects_bad_address_and_foreign_host(panel) -> None:
    _app, port = panel
    status, raw = call(
        port,
        "POST",
        "/api/settings",
        {
            "ollama_base_url": "file:///tmp/x",
            "dry_run": False,
            "mcp_port": 8791,
        },
    )
    assert status == 400
    assert "http://" in raw.decode("utf-8")
    status, _raw = call(port, "GET", "/api/state", host="evil.example")
    assert status == 403


def test_start_and_stop(panel) -> None:
    app, port = panel
    mcp = free_port()
    status, _raw = call(
        port,
        "POST",
        "/api/settings",
        {"dry_run": True, "mcp_port": mcp},
    )
    assert status == 200
    status, raw = call(port, "POST", "/api/start")
    body = json.loads(raw)
    assert status == 200
    assert body["running"] is True
    assert app.supervisor.pid() is not None
    status, raw = call(port, "POST", "/api/stop")
    assert status == 200
    assert json.loads(raw)["running"] is False
    assert not (app.data_dir / "mcp.pid").exists()


def test_log_is_scrubbed(panel) -> None:
    app, port = panel
    (app.data_dir).mkdir(parents=True, exist_ok=True)
    log_path = app.data_dir / "mcp.log"
    log_path.write_text("fejl sk-ant-abcdefghij1234567890 mere\n", encoding="utf-8")
    _status, raw = call(port, "GET", "/api/state")
    text = raw.decode("utf-8")
    assert "sk-ant-abcdefghij1234567890" not in text
    assert "[skjult]" in text


def test_child_env_prefers_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-shell-xxxx")
    monkeypatch.setenv("HYLDE_DISABLE_DOTENV", "1")
    apply_form(
        tmp_path / ".env",
        {
            "anthropic_api_key": "sk-ant-from-file-12345678",
            "dry_run": False,
            "mcp_port": 8791,
        },
    )
    env = child_env(tmp_path)
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-from-file-12345678"
    assert "sk-ant-from-shell-xxxx" not in env.values()
    assert "HYLDE_DISABLE_DOTENV" not in env
    settings = runtime_settings(tmp_path)
    assert settings.secret(settings.anthropic_api_key) == "sk-ant-from-file-12345678"


def test_unknown_env_line_is_kept(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("OTHER_TOOL=behold-mig\n", encoding="utf-8")
    apply_form(path, {"dry_run": False, "mcp_port": 8791})
    assert read_values(path)["OTHER_TOOL"] == "behold-mig"


def test_http_mcp_lists_tools(tmp_path: Path) -> None:
    from mcp import Client

    from hylde.supervisor import Supervisor

    port = free_port()
    apply_form(
        tmp_path / ".env",
        {"dry_run": True, "mcp_port": port},
    )
    supervisor = Supervisor(tmp_path)
    supervisor.start(port)
    try:
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            log = (tmp_path / "mcp.log").read_text(encoding="utf-8", errors="replace")
            pytest.fail(log[-2000:])

        async def names() -> set[str]:
            async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                listed = await client.list_tools()
                return {tool.name for tool in listed.tools}

        assert {"ask", "route", "second_opinion", "health"} <= asyncio.run(names())
    finally:
        supervisor.stop()


def test_ollama_llama_and_cloud_stay_separate(panel, tmp_path: Path) -> None:
    _app, port = panel
    cloud = "ollama-cloud-secret-value"
    status, raw = call(
        port,
        "POST",
        "/api/settings",
        {
            "ollama_model": "qwen2.5-coder:7b",
            "llama_enabled": True,
            "llama_base_url": "http://127.0.0.1:8080/v1",
            "llama_model": "local-gguf",
            "ollama_cloud_api_key": cloud,
            "ollama_cloud_model": "gemma4:31b",
            "dry_run": False,
            "mcp_port": 8791,
        },
    )
    text = raw.decode("utf-8")
    assert status == 200
    assert cloud not in text
    body = json.loads(text)
    assert body["settings"]["ollama_cloud_set"] is True
    assert body["settings"]["llama_enabled"] is True
    assert body["settings"]["ollama_model"] == "qwen2.5-coder:7b"
    saved = read_values(tmp_path / ".env")
    assert saved["OLLAMA_API_KEY"] == cloud
    assert saved["HYLDE_LOCAL_BASE_URL"] == "http://127.0.0.1:11434/v1"
    assert saved["HYLDE_LLAMA_BASE_URL"] == "http://127.0.0.1:8080/v1"
    cfg = runtime_settings(tmp_path)
    assert cfg.secret(cfg.ollama_cloud_api_key) == cloud
    assert cfg.llama_model == "local-gguf"
    assert cfg.local_model == "qwen2.5-coder:7b"


def test_legacy_llama_url_becomes_its_own_setting(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "HYLDE_LOCAL_BASE_URL=http://127.0.0.1:8080/v1\nHYLDE_LOCAL_MODEL=old-gguf\n",
        encoding="utf-8",
    )
    view = public_view(path)
    assert view["llama_enabled"] is True
    assert view["llama_model"] == "old-gguf"
    assert view["ollama_base_url"] == "http://127.0.0.1:11434/v1"
    assert view["ollama_model"] == ""
    cfg = runtime_settings(tmp_path)
    assert cfg.llama_enabled is True
    assert cfg.llama_model == "old-gguf"
    assert cfg.local_base_url == "http://127.0.0.1:11434/v1"
    assert cfg.local_model is None


def test_model_list_stays_on_this_machine(panel) -> None:
    _app, port = panel
    status, _raw = call(
        port,
        "POST",
        "/api/settings",
        {"ollama_base_url": "http://example.com/v1", "dry_run": False, "mcp_port": 8791},
    )
    assert status == 200
    status, raw = call(port, "GET", "/api/models?source=ollama")
    body = json.loads(raw)
    assert status == 200
    assert body["models"] == []
    assert "denne maskine" in body["error"]
    status, raw = call(port, "GET", "/api/models?source=cloud")
    body = json.loads(raw)
    assert status == 200
    assert "nøgle" in body["error"]
    status, raw = call(port, "GET", "/api/models?source=somewhere")
    assert status == 400


def test_llama_requires_a_model_when_enabled(panel) -> None:
    _app, port = panel
    status, raw = call(
        port,
        "POST",
        "/api/settings",
        {"llama_enabled": True, "dry_run": False, "mcp_port": 8791},
    )
    assert status == 400
    assert "llama.cpp" in raw.decode("utf-8")
