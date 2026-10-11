"""Hent OpenRouter-prior. Ændrer ikke kompetencetabellen."""

from __future__ import annotations

import asyncio
import sys

from hylde.config import Settings
from hylde.log import configure_logging, get_logger
from hylde.providers import HttpxTransport, ProviderError
from hylde.table import dump_json


async def pull_priors(settings: Settings | None = None) -> str:
    settings = settings or Settings()
    key = settings.secret(settings.openrouter_api_key)
    if not key:
        return "OPENROUTER_API_KEY mangler. Prior blev ikke hentet. Tabellen er uændret."
    transport = HttpxTransport()
    try:
        response = await transport.get(
            settings.openrouter_rankings_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
    except ProviderError as exc:
        return f"Kunne ikke hente prior: {exc}. Tabellen er uændret."
    finally:
        await transport.aclose()
    if response.status_code >= 400:
        return f"OpenRouter svarede {response.status_code}. Tabellen er uændret."
    payload = response.json()
    path = settings.data_dir / "priors" / "openrouter.json"
    dump_json(path, payload if isinstance(payload, dict) else {"data": payload})
    note = settings.data_dir / "priors" / "README.txt"
    note.write_text(
        "Dette er et prior fra OpenRouter (forbrug, ikke facit).\n"
        "Hylde ændrer ikke table.json automatisk. Sammenlign, kør tests, ret tabellen selv.\n",
        encoding="utf-8",
    )
    return f"Gemte prior i {path}. Kompetencetabellen er uændret."


def main() -> None:
    configure_logging("INFO")
    message = asyncio.run(pull_priors())
    get_logger().info(message)
    print(message, file=sys.stderr)


if __name__ == "__main__":
    main()
