"""MCP-server til Claude. Stdio. Få værktøjer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from hylde import __version__
from hylde.config import Settings
from hylde.health import health_report
from hylde.log import configure_logging, get_logger
from hylde.providers import HttpxTransport
from hylde.router import RouteError
from hylde.service import Service
from hylde.table import load_catalog, load_prompts

INSTRUCTIONS = """
Hylde vælger model pr. kald, så et helt projekt ikke kører på den dyreste model.
Værktøjer: route (gratis, intet kald), ask (ét kald), second_opinion (to dyre kald), health.
Kald ikke Hylde for noget du selv kan svare på.
Standard er hensigt=godt. spar = billigst. privat = kun lokal model.
bedst = kun når kvaliteten bærer prisen.
second_opinion kun når brugeren beder om et andet bud.
Sig altid hvilken model der svarede. Send aldrig API-nøgler.
""".strip()


@dataclass
class App:
    service: Service

    async def aclose(self) -> None:
        await self.service.aclose()


def build_app(settings: Settings | None = None) -> App:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    catalog = load_catalog(settings.table_path)
    prompts = load_prompts()
    service = Service(settings, catalog, prompts, HttpxTransport())
    return App(service)


def create_server() -> MCPServer:
    app_box: dict[str, App] = {}

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[App]:
        app = build_app()
        app_box["app"] = app
        get_logger().info("hylde %s klar", __version__)
        try:
            yield app
        finally:
            await app.aclose()

    mcp = MCPServer(
        "Hylde",
        instructions=INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
    )

    def current() -> App:
        if "app" not in app_box:
            app_box["app"] = build_app()
        return app_box["app"]

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Se ruten",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def route(
        prompt: str,
        intent: str = "godt",
        task: str = "auto",
        model: str = "",
    ) -> str:
        """Vis hvilken model Hylde ville bruge. Koster ingenting og kalder ingen model.

        intent: spar (billig), godt (standard), bedst (dyrt), privat (kun lokal).
        task: auto, classify, extract, draft, code, review, reason.
        model: tom, eller et hyldenavn: local, haiku, sonnet, opus, nano, mini, gpt.
        """
        try:
            report = current().service.route(prompt, intent=intent, task=task, model=model or None)
        except RouteError as exc:
            return f"Hylde kunne ikke vælge: {exc}"
        return report.render()

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Spørg en model",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        )
    )
    async def ask(
        prompt: str,
        intent: str = "godt",
        task: str = "auto",
        model: str = "",
    ) -> str:
        """Send opgaven til én model valgt efter pris og job.

        Brug spar til udtræk og kladder, godt til hverdag, bedst til svær kode eller tvivl,
        privat når teksten ikke må forlade maskinen. Lad model være tom medmindre brugeren
        navngav en hylde.
        """
        try:
            report = await current().service.ask(
                prompt, intent=intent, task=task, model=model or None
            )
        except RouteError as exc:
            return f"Hylde stoppede: {exc}"
        return report.render()

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Andet bud",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        )
    )
    async def second_opinion(prompt: str) -> str:
        """To cloud-modeller, ét samlet svar. Dyrere. Kun når brugeren beder om et andet bud."""
        try:
            report = await current().service.second_opinion(prompt)
        except RouteError as exc:
            return f"Hylde stoppede: {exc}"
        return report.render()

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Sundhed",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        )
    )
    async def health() -> str:
        """Sig om den lokale model svarer, og om cloud-nøgler er sat. Viser ikke selve nøglerne."""
        app = current()
        return await health_report(app.service.settings, app.service.catalog, app.service.transport)

    return mcp


mcp = create_server()
