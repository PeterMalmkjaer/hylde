"""Kompetencetabel og prompt-skabeloner. Netværket skriver aldrig tabellen om."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ProviderName = Literal["anthropic", "openai", "local"]
TierName = Literal["cheap", "mid", "frontier"]
SlotName = Literal["cheap", "balanced", "best"]


class Shelf(BaseModel):
    provider: ProviderName
    model: str = Field(min_length=1)
    tier: TierName


class DialoguePair(BaseModel):
    left: str
    right: str


class Catalog(BaseModel):
    version: str
    shelves: dict[str, Shelf]
    tasks: dict[str, dict[SlotName, str]]
    fallbacks: dict[str, list[str]]
    dialogue: DialoguePair


class PromptPack(BaseModel):
    version: str
    system: dict[TierName, str]
    dialogue_right: str
    dialogue_left: str
    max_tokens: dict[str, int]
    timeout_seconds: dict[str, float]


def package_data(name: str) -> Path:
    return Path(__file__).resolve().parent / "data" / name


def load_catalog(path: Path | None = None) -> Catalog:
    file = path or package_data("table.json")
    catalog = Catalog.model_validate_json(file.read_text(encoding="utf-8"))
    _check_refs(catalog)
    return catalog


def load_prompts(path: Path | None = None) -> PromptPack:
    file = path or package_data("prompts.json")
    return PromptPack.model_validate_json(file.read_text(encoding="utf-8"))


def _check_refs(catalog: Catalog) -> None:
    names = set(catalog.shelves)
    for task, slots in catalog.tasks.items():
        for slot, shelf in slots.items():
            if shelf not in names:
                raise ValueError(f"Opgave {task}/{slot} peger på ukendt hylde {shelf}")
    for source, chain in catalog.fallbacks.items():
        if source not in names:
            raise ValueError(f"Fallback for ukendt hylde {source}")
        missing = [item for item in chain if item not in names]
        if missing:
            raise ValueError(f"Fallback {source} peger på {missing}")
    for label, shelf in ("left", catalog.dialogue.left), ("right", catalog.dialogue.right):
        if shelf not in names:
            raise ValueError(f"Dialog-{label} peger på ukendt hylde {shelf}")


def dump_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
