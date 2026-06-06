"""SSH runner — SPEC §5.

Provides an async SSH connection wrapper that:
- Reuses a single asyncssh connection per session.
- Re-checks classify(command) and raises HardBlockedError on HARD_BLOCK
  (defense in depth, SPEC §0.2 and §5).
- Clamps per-command timeouts.
- Attempts exactly one reconnect on connection loss or timeout, then raises
  SSHRunnerError with a human-readable message.
- Never logs the private key or raw command output at INFO level.

Factory:
    make_ssh_runner(host, port=None, username=None, settings=None) -> SSHRunner
"""
from __future__ import annotations

import asyncio
import logging
import time
from types import TracebackType
from typing import Optional, Type

import asyncssh

from app.config import Settings, get_settings, resolve_ssh_connection
from app.models import CommandResult
from app.safety import Verdict, classify

logger = logging.getLogger("app.ssh_runner")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HardBlockedError(Exception):
    """Raised when classify() returns HARD_BLOCK — command must never run."""


class SSHRunnerError(Exception):
    """Raised on SSH connection or execution failures (timeouts, auth, etc.)."""


# ---------------------------------------------------------------------------
# SSHRunner
# ---------------------------------------------------------------------------


class SSHRunner:
    """Async SSH runner that reuses one connection per session.

    Parameters
    ----------
    host:
        Hostname or IP of the remote system.
    port:
        TCP port (default 22).
    username:
        SSH username.
    key_path:
        Path to the PEM/OpenSSH private key file. Its content is NEVER logged.
    connect_timeout:
        Seconds to wait for the initial connection handshake.
    command_timeout:
        Default per-command timeout in seconds.
    command_timeout_max:
        Hard upper bound for per-command timeouts (clamp target).
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        key_path: str,
        connect_timeout: int,
        command_timeout: int,
        command_timeout_max: int,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self._key_path = key_path  # kept private; never logged
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.command_timeout_max = command_timeout_max

        self._conn: asyncssh.SSHClientConnection | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the asyncssh connection.

        Raises SSHRunnerError on auth failure, host unreachable, or timeout.
        The private key path is never included in log messages.
        """
        logger.info(
            "Connecting to %s:%s as %s (connect_timeout=%ss)",
            self.host,
            self.port,
            self.username,
            self.connect_timeout,
        )
        try:
            self._conn = await asyncssh.connect(
                self.host,
                port=self.port,
                username=self.username,
                client_keys=[self._key_path],
                known_hosts=None,
                connect_timeout=self.connect_timeout,
            )
        except asyncssh.DisconnectError as exc:
            raise SSHRunnerError(
                f"SSH connection to {self.host}:{self.port} was disconnected "
                f"during handshake: {exc.reason}"
            ) from exc
        except asyncssh.PermissionDenied as exc:
            raise SSHRunnerError(
                f"SSH authentication failed for user '{self.username}' "
                f"on {self.host}:{self.port} — check the private key."
            ) from exc
        except (OSError, asyncio.TimeoutError) as exc:
            raise SSHRunnerError(
                f"Could not connect to {self.host}:{self.port}: {exc}"
            ) from exc
        except Exception as exc:
            raise SSHRunnerError(
                f"Unexpected error connecting to {self.host}:{self.port}: {exc}"
            ) from exc

        logger.info("SSH connection established to %s:%s", self.host, self.port)

    async def close(self) -> None:
        """Close the SSH connection cleanly."""
        if self._conn is not None:
            try:
                self._conn.close()
                await self._conn.wait_closed()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Non-fatal error while closing SSH connection: %s", exc)
            finally:
                self._conn = None
        logger.debug("SSH connection to %s closed.", self.host)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def run(
        self,
        command: str,
        *,
        timeout: Optional[int] = None,
    ) -> CommandResult:
        """Execute *command* over the reused SSH connection.

        Defense in depth (SPEC §0.2, §5): re-checks the safety classifier and
        raises HardBlockedError if the command is hard-blocked, regardless of
        how the runner was invoked.

        Parameters
        ----------
        command:
            The shell command string to execute remotely.
        timeout:
            Per-command timeout in seconds.  Clamped to [1, command_timeout_max].
            Defaults to command_timeout when None.

        Returns
        -------
        CommandResult
            exit_code, stdout, stderr, duration_ms — *not* redacted here; the
            orchestrator redacts before storage/display (SPEC §5).
        """
        # --- Defense-in-depth safety re-check ---
        classification = classify(command)
        if classification.verdict == Verdict.HARD_BLOCK:
            logger.warning(
                "SSH runner: HARD_BLOCK refused command. rule=%s reason=%s",
                classification.matched_rule,
                classification.reason,
            )
            raise HardBlockedError(
                f"Command refused (hard block): {classification.reason} "
                f"[rule: {classification.matched_rule}]"
            )

        # --- Clamp timeout ---
        effective_timeout = self._clamp_timeout(timeout)

        # --- Execute (with single reconnect on drop/timeout) ---
        try:
            return await self._execute(command, effective_timeout)
        except (asyncssh.misc.ConnectionLost, asyncio.TimeoutError, SSHRunnerError) as exc:
            logger.warning(
                "SSH runner: connection issue on first attempt (%s: %s). "
                "Attempting one reconnect.",
                type(exc).__name__,
                exc,
            )
            try:
                await self.close()
                await self.connect()
                return await self._execute(command, effective_timeout)
            except (asyncssh.misc.ConnectionLost, asyncio.TimeoutError) as retry_exc:
                raise SSHRunnerError(
                    f"Command execution failed after reconnect attempt "
                    f"({type(retry_exc).__name__}): {retry_exc}"
                ) from retry_exc
            except SSHRunnerError:
                raise
            except Exception as retry_exc:
                raise SSHRunnerError(
                    f"Unexpected error after reconnect: {retry_exc}"
                ) from retry_exc

    async def _execute(self, command: str, timeout_secs: int) -> CommandResult:
        """Run the command on the existing connection; measure wall-clock ms."""
        if self._conn is None:
            raise SSHRunnerError("No active SSH connection — call connect() first.")

        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._conn.run(command, check=False),
                timeout=float(timeout_secs),
            )
        except asyncio.TimeoutError as exc:
            raise asyncio.TimeoutError(
                f"Command timed out after {timeout_secs}s"
            ) from exc

        duration_ms = int((time.monotonic() - t0) * 1000)

        return CommandResult(
            exit_code=result.exit_status if result.exit_status is not None else -1,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clamp_timeout(self, timeout: Optional[int]) -> int:
        """Return an effective timeout clamped to [1, command_timeout_max]."""
        base = timeout if timeout is not None else self.command_timeout
        return max(1, min(base, self.command_timeout_max))

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "SSHRunner":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        await self.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_ssh_runner(
    host: str,
    port: Optional[int] = None,
    username: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> SSHRunner:
    """Create an SSHRunner resolving username/port from settings.

    Parameters
    ----------
    host:
        Hostname or IP to connect to.
    port:
        Optional port override; falls back to SSH_DEFAULT_PORT from settings.
    username:
        Optional username override; falls back to SSH_USERNAME from settings.
    settings:
        Optional Settings instance; uses get_settings() singleton when None.

    Returns
    -------
    SSHRunner
        A configured runner.  Call ``await runner.connect()`` before use.
    """
    if settings is None:
        settings = get_settings()

    resolved_username, resolved_port = resolve_ssh_connection(
        system_username=username,
        system_port=port,
        settings=settings,
    )

    return SSHRunner(
        host=host,
        port=resolved_port,
        username=resolved_username,
        key_path=settings.ssh_private_key_path,
        connect_timeout=settings.ssh_connect_timeout,
        command_timeout=settings.ssh_command_timeout,
        command_timeout_max=settings.ssh_command_timeout_max,
    )
