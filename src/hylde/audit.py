"""Kort audit. Ingen nøgler, og ingen prompt når kaldet er privat."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def write_audit(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = {key: value for key, value in record.items() if "key" not in key and "secret" not in key}
    if safe.get("private"):
        safe.pop("prompt", None)
    line = json.dumps({"ts": datetime.now(UTC).isoformat(), **safe}, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
