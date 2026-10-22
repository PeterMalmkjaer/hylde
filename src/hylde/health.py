"""Sundhed uden at røbe nøgler."""

from __future__ import annotations

from hylde.config import Settings
from hylde.providers import Transport, endpoint_up
from hylde.table import Catalog


async def health_report(settings: Settings, catalog: Catalog, transport: Transport) -> str:
    key = settings.secret(settings.local_api_key) or "ollama"
    ollama = await endpoint_up(transport, settings.local_base_url, key)
    ollama_model = settings.local_model or catalog.shelves["local"].model
    if settings.llama_enabled:
        llama_up = await endpoint_up(transport, settings.llama_base_url, key)
        llama_model = settings.llama_model or catalog.shelves["llama"].model
        llama = f"{'oppe' if llama_up else 'nede'} ({settings.llama_base_url}), model {llama_model}"
    else:
        llama = "slået fra"
    cloud_key = settings.secret(settings.ollama_cloud_api_key) is not None
    cloud_model = (settings.ollama_cloud_model or "").strip()
    if cloud_key and cloud_model:
        cloud = f"nøgle sat, model {cloud_model}"
    elif cloud_key:
        cloud = "nøgle sat, model mangler"
    else:
        cloud = "mangler"
    lines = [
        "Hylde sundhed",
        f"tabel: {catalog.version}",
        f"ollama: {'oppe' if ollama else 'nede'} ({settings.local_base_url})",
        f"ollama model: {ollama_model}",
        f"llama.cpp: {llama}",
        f"ollama cloud: {cloud}",
        f"anthropic nøgle: {'sat' if settings.secret(settings.anthropic_api_key) else 'mangler'}",
        f"openai nøgle: {'sat' if settings.secret(settings.openai_api_key) else 'mangler'}",
        f"openrouter nøgle: {'sat' if settings.secret(settings.openrouter_api_key) else 'mangler'}",
        f"dry-run: {'ja' if settings.dry_run else 'nej'}",
        "Nøglerne vises ikke. Mangler en nøgle: åbn Hylde-appen og gem den der.",
    ]
    return "\n".join(lines)
