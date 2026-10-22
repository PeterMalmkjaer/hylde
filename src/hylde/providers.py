"""Kald til Anthropic, OpenAI, Ollama, llama.cpp og Ollama Cloud."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx2

from hylde.config import Settings
from hylde.envfile import UserError, acceptable_model
from hylde.table import Shelf

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_CLOUD_TAGS = "https://ollama.com/api/tags"
_LIST_CAP = 200


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool, code: str = "") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.code = code


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


async def endpoint_up(transport: Transport, base_url: str, api_key: str) -> bool:
    url = base_url.rstrip("/") + "/models"
    try:
        response = await transport.get(
            url,
            headers={"Authorization": f"Bearer {api_key or 'ollama'}"},
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
    elif shelf.provider == "llama":
        base = settings.llama_base_url
        key = settings.secret(settings.local_api_key) or "ollama"
    elif shelf.provider == "ollama_cloud":
        base = settings.ollama_cloud_base_url
        key = settings.secret(settings.ollama_cloud_api_key)
        if not key:
            raise ProviderError("OLLAMA_API_KEY mangler.", retryable=False)
    else:
        base = settings.openai_base_url
        key = settings.secret(settings.openai_api_key)
        if not key:
            raise ProviderError("OPENAI_API_KEY mangler.", retryable=False)
    payload: dict[str, object] = {
        "model": shelf.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    # OpenAI's nyere modeller afviser max_tokens. Ollama og llama.cpp bruger det gamle felt.
    if shelf.provider == "openai":
        payload["max_completion_tokens"] = max_tokens
    else:
        payload["max_tokens"] = max_tokens
    response = await transport.post(
        base.rstrip("/") + "/chat/completions",
        json=payload,
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


def tags_url(openai_base: str) -> str:
    root = openai_base.strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root + "/api/tags"


def parse_model_names(payload: dict[str, object], *, shape: str) -> list[str]:
    if shape == "ollama":
        raw = payload.get("models")
        keys = ("name", "model")
    else:
        raw = payload.get("data")
        keys = ("id", "name")
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = ""
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                name = value.strip()
                break
        if not name or name in seen or not acceptable_model(name):
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= _LIST_CAP:
            break
    return names


def list_installed_models(settings: Settings, source: str) -> dict[str, object]:
    if source not in {"ollama", "llama", "cloud"}:
        raise UserError("Ukendt server.")
    if source == "cloud":
        key = settings.secret(settings.ollama_cloud_api_key)
        if not key:
            return {
                "models": [],
                "error": "Gem først en Ollama Cloud-nøgle. Det er ikke Anthropic-nøglen.",
            }
        url = _CLOUD_TAGS
        headers = {"Authorization": f"Bearer {key}"}
        shape = "ollama"
    elif source == "ollama":
        url = tags_url(settings.local_base_url)
        blocked = _loopback_error(url)
        if blocked:
            return {"models": [], "error": blocked}
        headers = {"Authorization": f"Bearer {settings.secret(settings.local_api_key) or 'ollama'}"}
        shape = "ollama"
    else:
        url = settings.llama_base_url.rstrip("/") + "/models"
        blocked = _loopback_error(url)
        if blocked:
            return {"models": [], "error": blocked}
        headers = {"Authorization": f"Bearer {settings.secret(settings.local_api_key) or 'ollama'}"}
        shape = "openai"
    try:
        names = asyncio.run(_fetch_names(url, headers, shape))
    except ProviderError as exc:
        if exc.code == "auth" and source == "cloud":
            return {
                "models": [],
                "error": "Ollama Cloud afviste nøglen. Brug nøglen fra ollama.com, ikke Anthropic.",
            }
        return {"models": [], "error": _down_message(source)}
    if not names:
        return {"models": [], "error": _empty_message(source)}
    return {"models": names, "error": ""}


async def _fetch_names(url: str, headers: dict[str, str], shape: str) -> list[str]:
    transport = HttpxTransport()
    try:
        response = await transport.get(url, headers=headers, timeout=4)
        if response.status_code in {401, 403}:
            raise ProviderError("Afvist.", retryable=False, code="auth")
        if response.status_code >= 400:
            raise ProviderError("Serveren svarede ikke.", retryable=False)
        return parse_model_names(_json(response), shape=shape)
    finally:
        await transport.aclose()


def _loopback_error(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in _LOOPBACK:
        return "Modellisten hentes kun fra en server på denne maskine. Skriv modelnavnet selv."
    return None


def _down_message(source: str) -> str:
    if source == "llama":
        return "llama.cpp svarer ikke. Tjek adressen, eller skriv modelnavnet selv."
    if source == "cloud":
        return "Ollama Cloud svarede ikke. Tjek nøglen hos Ollama."
    return "Ollama svarer ikke. Tænd Ollama-appen, og prøv igen."


def _empty_message(source: str) -> str:
    if source == "llama":
        return "llama.cpp viste ingen modeller. Skriv modelnavnet selv."
    if source == "cloud":
        return "Ollama Cloud viste ingen modeller. Skriv modelnavnet fra ollama.com."
    return "Ollama har ingen modeller. Kør: ollama pull qwen2.5-coder:7b"
