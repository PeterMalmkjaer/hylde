"""Sundhed uden at røbe nøgler."""

from __future__ import annotations

from hylde.config import Settings
from hylde.providers import Transport, local_up
from hylde.table import Catalog


async def health_report(settings: Settings, catalog: Catalog, transport: Transport) -> str:
    local = await local_up(transport, settings)
    lines = [
        "Hylde sundhed",
        f"tabel: {catalog.version}",
        f"lokal: {'oppe' if local else 'nede'} ({settings.local_base_url})",
        f"lokal model: {settings.local_model or catalog.shelves['local'].model}",
        f"anthropic nøgle: {'sat' if settings.secret(settings.anthropic_api_key) else 'mangler'}",
        f"openai nøgle: {'sat' if settings.secret(settings.openai_api_key) else 'mangler'}",
        f"openrouter nøgle: {'sat' if settings.secret(settings.openrouter_api_key) else 'mangler'}",
        f"dry-run: {'ja' if settings.dry_run else 'nej'}",
        "Nøglerne vises ikke. Mangler en nøgle: åbn Hylde-appen og gem den der.",
    ]
    return "\n".join(lines)
