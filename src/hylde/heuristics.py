"""Gæt opgavetype ud fra teksten, når brugeren ikke har sagt det."""

from __future__ import annotations

import re

TaskName = str

_CODE = re.compile(
    r"(```|\bdef\b|\bclass\b|\bfunction\b|\bimport\b|refaktor|refactor|pytest|traceback)",
    re.IGNORECASE,
)
_REVIEW = re.compile(r"\b(review|gennemg[åa]|code review|kig (den|det|koden))\b", re.IGNORECASE)
_EXTRACT = re.compile(r"\b(json|udtræk|extract|felter|skema|schema)\b", re.IGNORECASE)
_REASON = re.compile(
    r"\b(hvorfor|arkitektur|trade-?off|afvej|designvalg|ræsonn)\b",
    re.IGNORECASE,
)
_CLASSIFY = re.compile(r"\b(klassificer|kategori|ja eller nej|label)\b", re.IGNORECASE)


def guess_task(prompt: str) -> str:
    text = prompt.strip()
    if _CODE.search(text):
        return "code"
    if _REVIEW.search(text):
        return "review"
    if _EXTRACT.search(text):
        return "extract"
    if _REASON.search(text):
        return "reason"
    if _CLASSIFY.search(text) or len(text) < 280:
        return "classify"
    return "draft"
