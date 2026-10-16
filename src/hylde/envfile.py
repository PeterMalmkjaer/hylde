"""Læs og skriv ~/.hylde/.env. Nøglerne forlader ikke filen."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from pydantic import SecretStr

from hylde.config import Settings, settings_from_file

OLLAMA_URL = "http://127.0.0.1:11434/v1"
LLAMA_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MCP_PORT = 8766

FILE_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "HYLDE_LOCAL_BASE_URL",
    "HYLDE_LOCAL_MODEL",
    "HYLDE_LOCAL_API_KEY",
    "HYLDE_DRY_RUN",
    "HYLDE_MCP_PORT",
)
ENV_ALIASES = (
    "HYLDE_ANTHROPIC_API_KEY",
    "HYLDE_OPENAI_API_KEY",
    "HYLDE_OPENROUTER_API_KEY",
)
_SECRET_FILES = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
}
_TRUE = {"1", "true", "ja", "yes", "on"}


class UserError(Exception):
    """Tekst brugeren kan handle på."""


def read_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        values[key] = _unquote(raw_value.strip())
    return values


def write_values(path: Path, values: dict[str, str]) -> None:
    previous = read_values(path) if path.is_file() else {}
    unknown = {key: value for key, value in previous.items() if key not in FILE_KEYS}
    managed = {key: value for key, value in values.items() if key in FILE_KEYS and value != ""}
    lines = ["# Skrevet af Hylde. Nøglerne bruges, når du trykker Start."]
    for key in FILE_KEYS:
        if key in managed:
            lines.append(f"{key}={_quote(managed[key])}")
    if unknown:
        lines.append("")
        for key in sorted(unknown):
            lines.append(f"{key}={_quote(unknown[key])}")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def public_view(path: Path) -> dict[str, object]:
    values = read_values(path)
    url = values.get("HYLDE_LOCAL_BASE_URL", OLLAMA_URL).strip() or OLLAMA_URL
    local_key = values.get("HYLDE_LOCAL_API_KEY", "").strip()
    return {
        "anthropic_set": _filled(values.get("ANTHROPIC_API_KEY")),
        "anthropic_in_environment": _env_set("ANTHROPIC_API_KEY", "HYLDE_ANTHROPIC_API_KEY"),
        "openai_set": _filled(values.get("OPENAI_API_KEY")),
        "openai_in_environment": _env_set("OPENAI_API_KEY", "HYLDE_OPENAI_API_KEY"),
        "openrouter_set": _filled(values.get("OPENROUTER_API_KEY")),
        "openrouter_in_environment": _env_set("OPENROUTER_API_KEY", "HYLDE_OPENROUTER_API_KEY"),
        "local_kind": kind_for(url),
        "local_base_url": url,
        "local_model": values.get("HYLDE_LOCAL_MODEL", "").strip(),
        "local_api_key_set": bool(local_key) and local_key != "ollama",
        "dry_run": values.get("HYLDE_DRY_RUN", "").strip().lower() in _TRUE,
        "mcp_port": _stored_port(values.get("HYLDE_MCP_PORT")),
    }


def apply_form(path: Path, payload: dict[str, object]) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise UserError("Kunne ikke læse formularen.")
    current = read_values(path)
    updated = dict(current)
    for field, file_key in _SECRET_FILES.items():
        _apply_secret(updated, file_key, payload, field)
    kind = payload.get("local_kind", "ollama")
    if kind not in {"ollama", "llama", "custom"}:
        raise UserError("Vælg Ollama, llama.cpp eller en egen adresse.")
    if kind == "ollama":
        updated["HYLDE_LOCAL_BASE_URL"] = OLLAMA_URL
    elif kind == "llama":
        updated["HYLDE_LOCAL_BASE_URL"] = LLAMA_URL
    else:
        updated["HYLDE_LOCAL_BASE_URL"] = _http_url(payload.get("local_base_url"))
    model = _optional_text(payload.get("local_model"), "Modelnavnet")
    if model:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", model):
            raise UserError(
                "Modelnavnet må kun indeholde bogstaver, tal, punktum, kolon og bindestreg."
            )
        updated["HYLDE_LOCAL_MODEL"] = model
    else:
        updated.pop("HYLDE_LOCAL_MODEL", None)
    if payload.get("local_api_key_clear") is True:
        updated.pop("HYLDE_LOCAL_API_KEY", None)
    else:
        local_key = _optional_text(payload.get("local_api_key"), "Den lokale nøgle")
        if local_key:
            updated["HYLDE_LOCAL_API_KEY"] = local_key
    dry_run = payload.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise UserError("Prøvekørsel skal være til eller fra.")
    updated["HYLDE_DRY_RUN"] = "1" if dry_run else "0"
    updated["HYLDE_MCP_PORT"] = str(_port(payload.get("mcp_port", DEFAULT_MCP_PORT)))
    write_values(path, updated)
    return public_view(path)


def runtime_settings(data_dir: Path) -> Settings:
    values = read_values(data_dir / ".env")
    kwargs: dict[str, object] = {
        "local_base_url": values.get("HYLDE_LOCAL_BASE_URL", OLLAMA_URL) or OLLAMA_URL,
        "dry_run": values.get("HYLDE_DRY_RUN", "").strip().lower() in _TRUE,
    }
    for field, file_key in (
        ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        ("openai_api_key", "OPENAI_API_KEY"),
        ("openrouter_api_key", "OPENROUTER_API_KEY"),
    ):
        text = values.get(file_key, "").strip()
        if text:
            kwargs[field] = SecretStr(text)
    model = values.get("HYLDE_LOCAL_MODEL", "").strip()
    if model:
        kwargs["local_model"] = model
    local_key = values.get("HYLDE_LOCAL_API_KEY", "").strip()
    if local_key:
        kwargs["local_api_key"] = SecretStr(local_key)
    return settings_from_file(data_dir, **kwargs)


def child_env(data_dir: Path) -> dict[str, str]:
    """Miljø til MCP-processen. Filen vinder over nøgler i forældre-processen."""
    env = dict(os.environ)
    for key in (*FILE_KEYS, *ENV_ALIASES, "HYLDE_DISABLE_DOTENV"):
        env.pop(key, None)
    env["HYLDE_DATA_DIR"] = str(data_dir)
    for key, value in read_values(data_dir / ".env").items():
        if key in FILE_KEYS and value != "":
            env[key] = value
    return env


def kind_for(url: str) -> str:
    normal = url.rstrip("/")
    if normal == OLLAMA_URL.rstrip("/"):
        return "ollama"
    if normal == LLAMA_URL.rstrip("/"):
        return "llama"
    return "custom"


def _apply_secret(
    updated: dict[str, str],
    file_key: str,
    payload: dict[str, object],
    field: str,
) -> None:
    if payload.get(f"{field}_clear") is True:
        updated.pop(file_key, None)
        return
    raw = payload.get(field, "")
    if raw is None or raw == "":
        return
    if not isinstance(raw, str):
        raise UserError("Nøglen skal være tekst.")
    text = raw.strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    if not text:
        return
    if any(character.isspace() for character in text) or len(text) > 400:
        raise UserError("Nøglen ser forkert ud. Indsæt kun selve nøglen, uden mellemrum.")
    updated[file_key] = text


def _optional_text(value: object, label: str) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise UserError(f"{label} skal være tekst.")
    text = value.strip()
    if len(text) > 400 or any(character in text for character in "\n\r"):
        raise UserError(f"{label} er for langt eller indeholder et linjeskift.")
    return text


def _http_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UserError("Skriv adressen på den lokale server.")
    text = value.strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UserError("Adressen skal starte med http:// eller https://")
    return text.rstrip("/")


def _port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UserError("Porten skal være et helt tal.")
    if not 1024 <= value <= 65535:
        raise UserError("Porten skal ligge mellem 1024 og 65535.")
    return value


def _stored_port(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return DEFAULT_MCP_PORT
    try:
        return _port(int(raw.strip()))
    except (UserError, ValueError):
        return DEFAULT_MCP_PORT


def _filled(value: str | None) -> bool:
    return bool(value and value.strip())


def _env_set(*names: str) -> bool:
    return any(os.environ.get(name, "").strip() for name in names)


def _quote(value: str) -> str:
    if re.search(r'[\s#"\'\\]', value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        inner = raw[1:-1]
        return inner.replace("\\\\", "\x00").replace('\\"', '"').replace("\x00", "\\")
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1]
    return raw
