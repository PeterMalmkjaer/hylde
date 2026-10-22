"""Tests for routing, providers, and the MCP tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import pytest
from pydantic import SecretStr

from hylde.config import Settings
from hylde.heuristics import guess_task
from hylde.providers import parse_model_names
from hylde.router import RouteError, choose, looks_secret
from hylde.service import Service
from hylde.table import load_catalog, load_prompts


class FakeResponse:
    def __init__(self, status: int, payload: dict[str, object]) -> None:
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, object]:
        return self._payload


class FakeTransport:
    def __init__(self, handler: Callable[[str, dict[str, object]], FakeResponse]) -> None:
        self.handler = handler
        self.posts: list[str] = []

    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.posts.append(url)
        return self.handler(url, json)

    async def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
        return FakeResponse(200, {"data": [{"id": "local"}]})

    async def aclose(self) -> None:
        return None


def settings(tmp_path, **overrides: object) -> Settings:
    data = {
        "data_dir": tmp_path,
        "dry_run": False,
        "anthropic_api_key": None,
        "openai_api_key": None,
        "openrouter_api_key": None,
        "local_base_url": "http://127.0.0.1:11434/v1",
    }
    data.update(overrides)
    return Settings(**data)  # type: ignore[arg-type]


def service(tmp_path, transport: FakeTransport, **overrides: object) -> Service:
    return Service(settings(tmp_path, **overrides), load_catalog(), load_prompts(), transport)


def ok_openai(text: str = "svar") -> FakeResponse:
    return FakeResponse(
        200,
        {
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        },
    )


def ok_anthropic(text: str = "svar") -> FakeResponse:
    return FakeResponse(
        200,
        {
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 2, "output_tokens": 5},
        },
    )


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYLDE_DISABLE_DOTENV", "1")
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "HYLDE_ANTHROPIC_API_KEY",
        "HYLDE_OPENAI_API_KEY",
        "HYLDE_OPENROUTER_API_KEY",
        "HYLDE_DRY_RUN",
        "HYLDE_LOCAL_MODEL",
        "HYLDE_LLAMA_ENABLED",
        "HYLDE_LLAMA_MODEL",
        "HYLDE_OLLAMA_CLOUD_MODEL",
        "OLLAMA_API_KEY",
        "HYLDE_OLLAMA_CLOUD_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_guess_task() -> None:
    assert guess_task("def f():\n    return 1") == "code"
    assert guess_task("udtræk felter som json") == "extract"
    assert guess_task("hvorfor er den arkitektur bedre") == "reason"
    assert guess_task("review den her funktion") == "review"
    assert guess_task("hej") == "classify"


def test_secret_forces_local_even_if_cloud_keys_exist(tmp_path) -> None:
    catalog = load_catalog()
    cfg = settings(
        tmp_path,
        anthropic_api_key=SecretStr("sk-ant-test"),
        openai_api_key=SecretStr("sk-test"),
    )
    choice = choose(
        catalog,
        cfg,
        task="code",
        intent="bedst",
        model=None,
        force_private=looks_secret("nøglen er sk-ant-abcdefghij123456"),
    )
    assert choice.private
    assert choice.shelf.provider == "local"


def test_best_code_prefers_opus_when_configured(tmp_path) -> None:
    catalog = load_catalog()
    choice = choose(
        catalog,
        settings(
            tmp_path,
            anthropic_api_key=SecretStr("sk-ant-test"),
            openai_api_key=SecretStr("sk-test"),
        ),
        task="code",
        intent="bedst",
        model=None,
        force_private=False,
    )
    assert choice.shelf_key == "opus"


def test_cheap_classify_uses_local_without_keys(tmp_path) -> None:
    choice = choose(
        load_catalog(),
        settings(tmp_path),
        task="classify",
        intent="spar",
        model=None,
        force_private=False,
    )
    assert choice.shelf_key == "local"


def test_missing_opus_falls_back_to_gpt(tmp_path) -> None:
    choice = choose(
        load_catalog(),
        settings(tmp_path, openai_api_key=SecretStr("sk-test")),
        task="code",
        intent="bedst",
        model=None,
        force_private=False,
    )
    assert choice.shelf_key == "gpt"
    assert "ikke sat op" in choice.why


def test_unknown_shelf(tmp_path) -> None:
    with pytest.raises(RouteError):
        choose(
            load_catalog(),
            settings(tmp_path),
            task="code",
            intent="godt",
            model="claude",
            force_private=False,
        )


def test_short_note_stays_on_cheap_haiku(tmp_path) -> None:
    def handler(url: str, body: dict[str, object]) -> FakeResponse:
        assert body["model"] == "claude-haiku-4-5"
        return ok_anthropic("kort")

    app = service(
        tmp_path,
        FakeTransport(handler),
        anthropic_api_key=SecretStr("sk-ant-test"),
    )
    report = asyncio.run(app.ask("Skriv et kort notat om mødet."))
    assert report.shelf_key == "haiku"


def test_token_limit_field_follows_provider(tmp_path) -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def handler(url: str, body: dict[str, object]) -> FakeResponse:
        seen.append((url, body))
        if "anthropic" in url:
            return ok_anthropic("a")
        return ok_openai("o")

    app = service(
        tmp_path,
        FakeTransport(handler),
        anthropic_api_key=SecretStr("sk-ant-test"),
        openai_api_key=SecretStr("sk-test"),
        llama_enabled=True,
        llama_model="local-gguf",
        ollama_cloud_api_key=SecretStr("ollama-test-key"),
        ollama_cloud_model="gemma4:31b",
    )
    catalog = load_catalog()
    for key, shelf in catalog.shelves.items():
        asyncio.run(app._once(shelf, system="sys", prompt=f"opgave {key}", kind=shelf.tier))
    openai = [body for url, body in seen if "openai.com" in url]
    local = [body for url, body in seen if "11434" in url]
    llama = [body for url, body in seen if "8080" in url]
    cloud = [body for url, body in seen if "ollama.com" in url]
    anthropic = [body for url, body in seen if "anthropic" in url]
    assert len(openai) == 3
    assert len(local) == 1
    assert len(llama) == 1
    assert len(cloud) == 1
    assert len(anthropic) == 3
    assert all("max_completion_tokens" in body and "max_tokens" not in body for body in openai)
    for body in (*local, *llama, *cloud, *anthropic):
        assert "max_tokens" in body and "max_completion_tokens" not in body


def test_ask_posts_to_anthropic_for_balanced_draft(tmp_path) -> None:
    def handler(url: str, body: dict[str, object]) -> FakeResponse:
        assert "anthropic" in url
        assert body["model"] == "claude-sonnet-5"
        return ok_anthropic("kladde")

    transport = FakeTransport(handler)
    app = service(tmp_path, transport, anthropic_api_key=SecretStr("sk-ant-test"))
    prompt = "Skriv et kort notat om mødet i morgen klokken ti med hele teamet."
    report = asyncio.run(app.ask(prompt, task="draft"))
    assert report.shelf_key == "sonnet"
    assert report.text == "kladde"
    assert "anthropic.com" in transport.posts[0]
    audit = (tmp_path / "audit.ndjson").read_text(encoding="utf-8")
    assert "sk-ant" not in audit
    assert "notat" in audit


def test_private_audit_drops_prompt(tmp_path) -> None:
    transport = FakeTransport(lambda url, body: ok_openai("ok"))
    app = service(tmp_path, transport)
    report = asyncio.run(app.ask("hemmelig plan", intent="privat"))
    assert report.private
    assert report.provider == "local"
    line = json.loads((tmp_path / "audit.ndjson").read_text(encoding="utf-8"))
    assert "prompt" not in line
    assert "11434" in transport.posts[0]


def test_cloud_is_opt_in_and_never_private(tmp_path) -> None:
    catalog = load_catalog()
    with pytest.raises(RouteError):
        choose(
            catalog,
            settings(tmp_path),
            task="reason",
            intent="godt",
            model="cloud",
            force_private=False,
        )
    choice = choose(
        catalog,
        settings(
            tmp_path,
            ollama_cloud_api_key=SecretStr("ollama-test-key"),
            ollama_cloud_model="gemma4:31b",
        ),
        task="reason",
        intent="godt",
        model="cloud",
        force_private=False,
    )
    assert choice.shelf_key == "cloud"
    assert choice.shelf.provider == "ollama_cloud"
    assert choice.shelf.model == "gemma4:31b"
    private = choose(
        catalog,
        settings(
            tmp_path,
            ollama_cloud_api_key=SecretStr("ollama-test-key"),
            ollama_cloud_model="gemma4:31b",
        ),
        task="reason",
        intent="privat",
        model="cloud",
        force_private=False,
    )
    assert private.private
    assert private.shelf.provider == "local"


def test_llama_can_be_named_while_ollama_stays_up(tmp_path) -> None:
    choice = choose(
        load_catalog(),
        settings(tmp_path, llama_enabled=True, llama_model="my-gguf"),
        task="code",
        intent="spar",
        model="llama",
        force_private=False,
    )
    assert choice.shelf_key == "llama"
    assert choice.shelf.model == "my-gguf"
    default = choose(
        load_catalog(),
        settings(tmp_path, llama_enabled=True, llama_model="my-gguf"),
        task="classify",
        intent="spar",
        model=None,
        force_private=False,
    )
    assert default.shelf_key == "local"


def test_private_tries_llama_when_ollama_fails(tmp_path) -> None:
    def handler(url: str, body: dict[str, object]) -> FakeResponse:
        if "11434" in url:
            return FakeResponse(404, {"error": {"message": "model not found"}})
        assert "8080" in url
        assert body["model"] == "my-gguf"
        assert "max_tokens" in body
        return ok_openai("fra llama")

    app = service(tmp_path, FakeTransport(handler), llama_enabled=True, llama_model="my-gguf")
    report = asyncio.run(app.ask("hemmelig plan", intent="privat"))
    assert report.provider == "llama"
    assert report.text == "fra llama"
    assert report.private


def test_model_names_come_from_ollama_tags() -> None:
    names = parse_model_names(
        {
            "models": [
                {"name": "qwen2.5-coder:7b"},
                {"name": "qwen2.5-coder:7b"},
                {"name": "../evil"},
                {"model": "gemma4:31b"},
            ]
        },
        shape="ollama",
    )
    assert names == ["qwen2.5-coder:7b", "gemma4:31b"]
    openai_names = parse_model_names(
        {"data": [{"id": "local-gguf"}, {"id": "has space"}]},
        shape="openai",
    )
    assert openai_names == ["local-gguf"]


def test_provider_error_falls_through_once(tmp_path) -> None:
    def handler(url: str, body: dict[str, object]) -> FakeResponse:
        if "anthropic" in url:
            return FakeResponse(500, {"error": {"message": "nede"}})
        return ok_openai("fra gpt")

    app = service(
        tmp_path,
        FakeTransport(handler),
        anthropic_api_key=SecretStr("sk-ant-test"),
        openai_api_key=SecretStr("sk-test"),
    )
    report = asyncio.run(app.ask("def f():\n    return 1\n# forklar", intent="godt", task="code"))
    assert report.shelf_key == "mini"
    assert report.text == "fra gpt"


def test_pinned_model_does_not_fallback(tmp_path) -> None:
    app = service(
        tmp_path,
        FakeTransport(lambda url, body: FakeResponse(400, {"error": {"message": "ukendt model"}})),
        anthropic_api_key=SecretStr("sk-ant-test"),
        openai_api_key=SecretStr("sk-test"),
    )
    with pytest.raises(RouteError):
        asyncio.run(app.ask("hej", model="haiku"))


def test_second_opinion_two_calls(tmp_path) -> None:
    def handler(url: str, body: dict[str, object]) -> FakeResponse:
        if "openai" in url:
            return ok_openai("første")
        return ok_anthropic("samlet")

    app = service(
        tmp_path,
        FakeTransport(handler),
        anthropic_api_key=SecretStr("sk-ant-test"),
        openai_api_key=SecretStr("sk-test"),
    )
    report = asyncio.run(app.second_opinion("Skal vi vælge kø eller tråde til jobbet?"))
    assert report.text == "samlet"
    assert report.second_model is not None
    assert "openai" in report.second_model
    assert len(app.transport.posts) == 2  # type: ignore[attr-defined]


def test_second_opinion_refuses_secrets(tmp_path) -> None:
    app = service(
        tmp_path,
        FakeTransport(lambda url, body: ok_openai("nej")),
        anthropic_api_key=SecretStr("sk-ant-test"),
        openai_api_key=SecretStr("sk-test"),
    )
    with pytest.raises(RouteError):
        asyncio.run(app.second_opinion("her er sk-ant-abcdefghij1234567890"))


def test_route_does_not_call_network(tmp_path) -> None:
    transport = FakeTransport(lambda url, body: ok_openai("nej"))
    app = service(tmp_path, transport, anthropic_api_key=SecretStr("sk-ant-test"))
    report = app.route("def main(): pass", intent="bedst", task="code")
    assert report.shelf_key == "opus"
    assert transport.posts == []
    assert "Ingen model blev kaldt" in report.text


def test_catalog_rejects_unknown_shelf(tmp_path) -> None:
    raw = json.loads(load_catalog().model_dump_json())
    raw["tasks"]["code"]["best"] = "findes-ikke"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(path)


def test_dry_run_skips_http(tmp_path) -> None:
    transport = FakeTransport(lambda url, body: ok_openai("nej"))
    app = service(tmp_path, transport, dry_run=True)
    report = asyncio.run(app.ask("hej", intent="spar"))
    assert report.text.startswith("[dry-run")
    assert transport.posts == []


def test_refresh_does_not_touch_table(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hylde.refresh import pull_priors

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    cfg = settings(tmp_path, openrouter_api_key=SecretStr("sk-or-test"))

    class RefreshTransport(FakeTransport):
        async def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
            assert headers["Authorization"] == "Bearer sk-or-test"
            return FakeResponse(200, {"data": [{"model_permaslug": "openai/gpt-test"}]})

    async def run() -> str:
        from hylde import refresh as refresh_mod

        original = refresh_mod.HttpxTransport
        refresh_mod.HttpxTransport = lambda: RefreshTransport(lambda url, body: ok_openai())  # type: ignore[assignment]
        try:
            return await pull_priors(cfg)
        finally:
            refresh_mod.HttpxTransport = original

    message = asyncio.run(run())
    assert "uændret" in message
    saved = json.loads((tmp_path / "priors" / "openrouter.json").read_text(encoding="utf-8"))
    assert saved["data"][0]["model_permaslug"] == "openai/gpt-test"


def test_stdout_log_stays_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    from hylde.log import configure_logging

    configure_logging("INFO")
    import logging

    logging.getLogger("hylde").info("kun-stderr")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "kun-stderr" in captured.err


def test_mcp_ask_dry_run(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYLDE_DRY_RUN", "1")
    monkeypatch.setenv("HYLDE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HYLDE_DISABLE_DOTENV", "1")

    from mcp import Client

    from hylde.server import create_server

    async def run() -> str:
        async with Client(create_server()) as client:
            result = await client.call_tool("ask", {"prompt": "hej", "intent": "spar"})
            text = result.content[0].text  # type: ignore[union-attr]
            return text

    rendered = asyncio.run(run())
    assert "dry-run" in rendered
    assert "model:" in rendered
