"""Kald til Anthropic, OpenAI og en lokal OpenAI-kompatibel server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx2

from hylde.config import Settings
from hylde.table import Shelf


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Completion:
    text: str
    input_tokens: int | None
    output_tokens: int | None


class Transport(Protocol):
    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx2.Response: ...

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx2.Response: ...

    async def aclose(self) -> None: ...


class HttpxTransport:
    def __init__(self) -> None:
        self._client = httpx2.AsyncClient()

    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx2.Response:
        try:
            return await self._client.post(url, json=json, headers=headers, timeout=timeout)
        except httpx2.TimeoutException as exc:
            raise ProviderError("Kaldet tog for lang tid.", retryable=True) from exc
        except httpx2.HTTPError as exc:
            raise ProviderError("Netværksfejl mod modellen.", retryable=True) from exc

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx2.Response:
        try:
            return await self._client.get(url, headers=headers, timeout=timeout)
        except httpx2.HTTPError as exc:
            raise ProviderError("Kunne ikke nå serveren.", retryable=True) from exc

    async def aclose(self) -> None:
        await self._client.aclose()


async def complete(
    transport: Transport,
    settings: Settings,
    shelf: Shelf,
    *,
    system: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
) -> Completion:
    if shelf.provider == "anthropic":
        return await _anthropic(
            transport,
            settings,
            shelf,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    return await _openai_compat(
        transport,
        settings,
        shelf,
        system=system,
        prompt=prompt,
        max_tokens=max_tokens,
        timeout=timeout,
    )


async def local_up(transport: Transport, settings: Settings) -> bool:
    url = settings.local_base_url.rstrip("/") + "/models"
    try:
        response = await transport.get(
            url,
            headers={
                "Authorization": f"Bearer {settings.secret(settings.local_api_key) or 'ollama'}",
            },
            timeout=3,
        )
    except ProviderError:
        return False
    return response.status_code < 500


async def _anthropic(
    transport: Transport,
    settings: Settings,
    shelf: Shelf,
    *,
    system: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
) -> Completion:
    key = settings.secret(settings.anthropic_api_key)
    if not key:
        raise ProviderError("ANTHROPIC_API_KEY mangler.", retryable=False)
    response = await transport.post(
        settings.anthropic_base_url.rstrip("/") + "/v1/messages",
        json={
            "model": shelf.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        timeout=timeout,
    )
    body = _json(response)
    if response.status_code >= 400:
        raise ProviderError(
            _api_message(body, response.status_code),
            retryable=response.status_code >= 429,
        )
    blocks = body.get("content")
    text = ""
    if isinstance(blocks, list):
        text = "\n".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return Completion(
        text=text or "(tomt svar)",
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
    )


async def _openai_compat(
    transport: Transport,
    settings: Settings,
    shelf: Shelf,
    *,
    system: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
) -> Completion:
    if shelf.provider == "local":
        base = settings.local_base_url
        key = settings.secret(settings.local_api_key) or "ollama"
    else:
        base = settings.openai_base_url
        key = settings.secret(settings.openai_api_key)
        if not key:
            raise ProviderError("OPENAI_API_KEY mangler.", retryable=False)
    response = await transport.post(
        base.rstrip("/") + "/chat/completions",
        json={
            "model": shelf.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        },
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        timeout=timeout,
    )
    body = _json(response)
    if response.status_code >= 400:
        raise ProviderError(
            _api_message(body, response.status_code),
            retryable=response.status_code >= 429,
        )
    text = ""
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            text = str(message.get("content") or "").strip()
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return Completion(
        text=text or "(tomt svar)",
        input_tokens=_int(usage.get("prompt_tokens")),
        output_tokens=_int(usage.get("completion_tokens")),
    )


def _json(response: httpx2.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        return {"error": response.text[:300]}
    return payload if isinstance(payload, dict) else {"error": str(payload)[:300]}


def _api_message(body: dict[str, object], status: int) -> str:
    error = body.get("error")
    if isinstance(error, dict) and error.get("message"):
        return f"Modellen svarede {status}: {error['message']}"
    if isinstance(error, str):
        return f"Modellen svarede {status}: {error}"
    return f"Modellen svarede {status}."


def _int(value: object) -> int | None:
    return value if isinstance(value, int) else None
