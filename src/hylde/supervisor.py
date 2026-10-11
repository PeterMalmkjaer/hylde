"""Start og stop MCP-processen. Den lytter kun på 127.0.0.1."""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from hylde.envfile import UserError, child_env

_SECRET = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]+|sk-or-v1-[A-Za-z0-9_\-]+|sk-[A-Za-z0-9]{12,}"
    r"|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY)"
)


def default_mcp_command(port: int) -> list[str]:
    return [sys.executable, "-m", "hylde", "--mcp-http", str(port)]


class Supervisor:
    def __init__(
        self,
        data_dir: Path,
        command_for_port: Callable[[int], list[str]] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.command_for_port = command_for_port or default_mcp_command
        self._lock = threading.Lock()
        self._popen: subprocess.Popen[bytes] | None = None
        self._spawned_pid: int | None = None
        self._external_pid: int | None = None
        self._adopt()

    def running(self) -> bool:
        return self.pid() is not None

    def pid(self) -> int | None:
        with self._lock:
            if self._popen is not None and self._popen.poll() is None:
                return self._popen.pid
            if self._external_alive():
                return self._external_pid
            return None

    def start(self, port: int) -> None:
        with self._lock:
            if self._is_running_locked():
                raise UserError(
                    "Hylde kører allerede. Tryk Genstart, hvis du har gemt nye indstillinger."
                )
            if not _port_free(port):
                raise UserError(f"Port {port} er optaget. Vælg en anden under Flere valg, og gem.")
            self.data_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.data_dir / "mcp.log"
            env = child_env(self.data_dir)
            with log_path.open("ab") as log:
                proc = subprocess.Popen(
                    self.command_for_port(port),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=env,
                )
            time.sleep(0.35)
            if proc.poll() is not None:
                raise UserError("Hylde stoppede med det samme.\n" + log_tail(log_path))
            self._popen = proc
            self._spawned_pid = proc.pid
            self._external_pid = None
            _write_pid(self._pidfile(), proc.pid, port)

    def stop(self, *, only_spawned: bool = False) -> None:
        with self._lock:
            pid = self._pid_locked()
            if pid is None:
                self._clear_locked()
                return
            if only_spawned and pid != self._spawned_pid:
                return
            _terminate(pid)
            if self._popen is not None:
                try:
                    self._popen.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    _kill(pid)
                    self._popen.wait(timeout=2)
            self._clear_locked()

    def _adopt(self) -> None:
        path = self._pidfile()
        if not path.is_file():
            return
        try:
            pid = int(path.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            path.unlink(missing_ok=True)
            return
        if pid > 0 and _alive(pid) and _is_hylde_mcp(pid):
            self._external_pid = pid
            return
        path.unlink(missing_ok=True)

    def _pidfile(self) -> Path:
        return self.data_dir / "mcp.pid"

    def _is_running_locked(self) -> bool:
        return self._pid_locked() is not None

    def _pid_locked(self) -> int | None:
        if self._popen is not None and self._popen.poll() is None:
            return self._popen.pid
        if self._external_alive():
            return self._external_pid
        return None

    def _external_alive(self) -> bool:
        return (
            self._external_pid is not None
            and _alive(self._external_pid)
            and _is_hylde_mcp(self._external_pid)
        )

    def _clear_locked(self) -> None:
        self._popen = None
        self._spawned_pid = None
        self._external_pid = None
        self._pidfile().unlink(missing_ok=True)


def log_tail(path: Path, limit: int = 2500) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return _scrub(text[-limit:])


def _scrub(text: str) -> str:
    return _SECRET.sub("[skjult]", text)


def _write_pid(path: Path, pid: int, port: int) -> None:
    path.write_text(f"{pid} {port}\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _is_hylde_mcp(pid: int) -> bool:
    try:
        command = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            timeout=2,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return "hylde" in command and "--mcp-http" in command


def _terminate(pid: int) -> None:
    _signal(pid, signal.SIGTERM)


def _kill(pid: int) -> None:
    _signal(pid, signal.SIGKILL)


def _signal(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            return
