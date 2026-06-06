"""SSH command runner (asyncssh).

One connection per run. Each approved command runs via ``conn.run`` with a
per-command timeout; stdout and stderr are captured separately plus a combined
view for the agent. The connection disables host-key checking (hackathon VMs)
and supports a hard cancel for the STOP control.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import asyncssh


class SSHError(Exception):
    pass


@dataclass
class CommandResult:
    command: str
    stdout: str
    stderr: str
    exit_code: Optional[int]
    timed_out: bool = False

    @property
    def combined(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr if not self.stdout else f"[stderr]\n{self.stderr}")
        text = "\n".join(parts).strip()
        if self.timed_out:
            text = (text + "\n" if text else "") + "[command timed out]"
        return text


class SSHRunner:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        key_path: str,
        connect_timeout: int = 15,
        command_timeout: int = 45,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.key_path = key_path
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self._conn: Optional[asyncssh.SSHClientConnection] = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        try:
            self._conn = await asyncio.wait_for(
                asyncssh.connect(
                    self.host,
                    port=self.port,
                    username=self.username,
                    client_keys=[self.key_path],
                    known_hosts=None,  # hackathon VMs; we trust the provided target
                ),
                timeout=self.connect_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise SSHError(f"SSH connect to {self.host}:{self.port} timed out") from exc
        except (OSError, asyncssh.Error) as exc:
            raise SSHError(f"SSH connect to {self.host}:{self.port} failed: {exc}") from exc

    async def run_command(self, command: str, timeout: Optional[int] = None) -> CommandResult:
        if self._conn is None:
            raise SSHError("Not connected")
        timeout = timeout or self.command_timeout
        async with self._lock:
            try:
                result = await asyncio.wait_for(
                    self._conn.run(command, check=False), timeout=timeout
                )
            except asyncio.TimeoutError:
                return CommandResult(command, "", "", None, timed_out=True)
            except asyncssh.Error as exc:
                return CommandResult(command, "", f"SSH error: {exc}", None)

        stdout = result.stdout if isinstance(result.stdout, str) else (result.stdout or b"").decode("utf-8", "replace")
        stderr = result.stderr if isinstance(result.stderr, str) else (result.stderr or b"").decode("utf-8", "replace")
        return CommandResult(command, stdout, stderr, result.exit_status)

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            try:
                await self._conn.wait_closed()
            except Exception:
                pass
            self._conn = None
