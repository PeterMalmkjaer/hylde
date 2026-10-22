"""Vælg hylde. Ingen HTTP her."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hylde.config import Settings
from hylde.table import Catalog, Shelf

INTENTS = {
    "spar": "cheap",
    "cheap": "cheap",
    "billig": "cheap",
    "godt": "balanced",
    "balanced": "balanced",
    "bedst": "best",
    "best": "best",
    "privat": "private",
    "private": "private",
}

ON_MACHINE = frozenset({"local", "llama"})

SECRET = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{8,}|sk-or-v1-[A-Za-z0-9_\-]{8,}"
    r"|sk-[A-Za-z0-9]{20,}|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY|AKIA[0-9A-Z]{16})"
)


class RouteError(Exception):
    """Brugeren kan gøre noget ved det."""


@dataclass(frozen=True)
class Choice:
    shelf_key: str
    shelf: Shelf
    task: str
    intent: str
    private: bool
    pinned: bool
    why: str
    chain: tuple[str, ...]


def normalize_intent(intent: str | None) -> str:
    key = (intent or "godt").strip().lower()
    if key not in INTENTS:
        known = ", ".join(sorted({"spar", "godt", "bedst", "privat"}))
        raise RouteError(f"Ukendt hensigt {intent!r}. Brug {known}.")
    return INTENTS[key]


def looks_secret(prompt: str) -> bool:
    return SECRET.search(prompt) is not None


def resolve_model(settings: Settings, shelf_key: str, shelf: Shelf) -> Shelf:
    if shelf_key == "local" and settings.local_model:
        return shelf.model_copy(update={"model": settings.local_model})
    if shelf_key == "llama" and settings.llama_model:
        return shelf.model_copy(update={"model": settings.llama_model})
    if shelf_key == "cloud" and settings.ollama_cloud_model:
        return shelf.model_copy(update={"model": settings.ollama_cloud_model})
    return shelf


def available(settings: Settings, shelf: Shelf) -> bool:
    if shelf.provider == "local":
        return bool(settings.local_base_url.strip())
    if shelf.provider == "llama":
        return settings.llama_enabled and bool((settings.llama_model or "").strip())
    if shelf.provider == "ollama_cloud":
        return settings.secret(settings.ollama_cloud_api_key) is not None and bool(
            (settings.ollama_cloud_model or "").strip()
        )
    if shelf.provider == "anthropic":
        return settings.secret(settings.anthropic_api_key) is not None
    if shelf.provider == "openai":
        return settings.secret(settings.openai_api_key) is not None
    return False


def choose(
    catalog: Catalog,
    settings: Settings,
    *,
    task: str,
    intent: str | None,
    model: str | None,
    force_private: bool,
) -> Choice:
    slot = normalize_intent(intent)
    private = force_private or slot == "private"
    pinned = False
    task_name = task

    if private:
        chain = ("local", "llama")
        why = (
            "Privat: kun modeller på maskinen (Ollama, derefter llama.cpp). "
            "Intet sendes til en cloud-API."
        )
    elif model and model.strip():
        pinned = True
        key = model.strip()
        if key not in catalog.shelves:
            known = ", ".join(sorted(catalog.shelves))
            raise RouteError(f"Ukendt model {key!r}. Hylder: {known}.")
        chain = (key,)
        why = f"Du bad om hylden {key}."
    else:
        if task not in catalog.tasks:
            known = ", ".join(sorted(catalog.tasks))
            raise RouteError(f"Ukendt opgave {task!r}. Brug {known}.")
        preferred = catalog.tasks[task][slot]  # type: ignore[index]
        chain = (preferred, *catalog.fallbacks.get(preferred, ()))
        why = f"Opgave {task}, hensigt {slot}: foretrækker {preferred}."
        task_name = task

    if private:
        local = tuple(key for key in chain if catalog.shelves[key].provider in ON_MACHINE)
        chain = local or ("local",)
        if not pinned:
            why = (
                "Privat: kun modeller på maskinen (Ollama, derefter llama.cpp). "
                "Intet sendes til en cloud-API."
            )

    picked = _first_available(catalog, settings, chain)
    if picked is None:
        raise RouteError(_missing_message(catalog, settings, chain))

    shelf = resolve_model(settings, picked, catalog.shelves[picked])
    if picked != chain[0]:
        why += f" {chain[0]} er ikke sat op, så jeg bruger {picked}."
    return Choice(
        shelf_key=picked,
        shelf=shelf,
        task=task_name,
        intent="private" if private else slot,
        private=private,
        pinned=pinned,
        why=why,
        chain=chain,
    )


def dialogue_pair(catalog: Catalog, settings: Settings) -> tuple[Choice, Choice]:
    left = choose(
        catalog,
        settings,
        task="reason",
        intent="godt",
        model=catalog.dialogue.left,
        force_private=False,
    )
    right = choose(
        catalog,
        settings,
        task="reason",
        intent="godt",
        model=catalog.dialogue.right,
        force_private=False,
    )
    if left.shelf.provider in ON_MACHINE or right.shelf.provider in ON_MACHINE:
        raise RouteError("Andet bud kræver to cloud-modeller. Sæt både Anthropic- og OpenAI-nøgle.")
    if left.shelf_key == right.shelf_key:
        raise RouteError("Andet bud har ikke to forskellige hylder lige nu.")
    return left, right


def _first_available(catalog: Catalog, settings: Settings, chain: tuple[str, ...]) -> str | None:
    for key in chain:
        shelf = catalog.shelves.get(key)
        if shelf and available(settings, shelf):
            return key
    return None


def _missing_message(catalog: Catalog, settings: Settings, chain: tuple[str, ...]) -> str:
    needs = sorted({catalog.shelves[key].provider for key in chain if key in catalog.shelves})
    bits = []
    if "anthropic" in needs and not settings.secret(settings.anthropic_api_key):
        bits.append("ANTHROPIC_API_KEY")
    if "openai" in needs and not settings.secret(settings.openai_api_key):
        bits.append("OPENAI_API_KEY")
    if "local" in needs and not settings.local_base_url.strip():
        bits.append("HYLDE_LOCAL_BASE_URL")
    if "llama" in needs and not (settings.llama_enabled and (settings.llama_model or "").strip()):
        bits.append("llama.cpp (slå den til og vælg model)")
    if "ollama_cloud" in needs and not settings.secret(settings.ollama_cloud_api_key):
        bits.append("OLLAMA_API_KEY")
    elif "ollama_cloud" in needs and not (settings.ollama_cloud_model or "").strip():
        bits.append("Ollama Cloud-model")
    detail = ", ".join(bits) if bits else "en tilgængelig model"
    return f"Ingen model i kæden er sat op ({detail}). Se README: nøgler ligger i ~/.hylde/.env."
