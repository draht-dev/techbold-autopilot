"""mock_ssh.py — Deterministic fake SSH runner for offline/demo/testing.

This module does NOT connect to any real host.  It implements the same async
interface as ``SSHRunner`` (connect, run, close, async context manager) and
returns canned ``CommandResult`` values for a single sample incident:

    **nginx is stopped AND not enabled.**

The internal state machine tracks active/enabled flags so the orchestrator's
APPLY → VALIDATE → PERSIST_CHECK phases observe a real state transition when
the agent issues the correct fix commands.

State transitions
-----------------
- ``systemctl enable --now nginx``  → active=True, enabled=True
- ``systemctl enable nginx``        → enabled=True
- ``systemctl start nginx``         → active=True
- ``systemctl restart nginx``       → active=True (enabled unchanged)
- ``systemctl stop nginx``          → active=False
- ``systemctl disable nginx``       → enabled=False
- ``systemctl disable --now nginx`` → active=False, enabled=False
- ``daemon-reload``                 → no state change, exit 0

For unrecognised commands the mock returns exit 0 with empty stdout so it
does not break the orchestrator loop on exploratory read commands.

Defense in depth: re-checks classify() and raises HardBlockedError on
HARD_BLOCK (mirrors the real runner — SPEC §0.2, §5, §12).

Usage::

    from mocks.mock_ssh import MockSSHRunner

    async with MockSSHRunner() as runner:
        result = await runner.run("systemctl is-active nginx")
        print(result.stdout)   # "inactive"
"""
from __future__ import annotations

import re
from types import TracebackType
from typing import Optional, Type

from app.models import CommandResult
from app.safety import Verdict, classify
from app.ssh_runner import HardBlockedError


# ---------------------------------------------------------------------------
# Canned multi-line status output helpers
# ---------------------------------------------------------------------------

_STATUS_ACTIVE = """\
● nginx.service - A high performance web server and a reverse proxy server
     Loaded: loaded (/lib/systemd/system/nginx.service; enabled; vendor preset: enabled)
     Active: active (running) since Mon 2026-06-06 10:00:00 UTC; 5min ago
    Process: 1234 ExecStartPre=/usr/sbin/nginx -t -q -g daemon on; master_process on; (code=exited, status=0/SUCCESS)
   Main PID: 1235 (nginx)
      Tasks: 2 (limit: 4915)
     Memory: 4.0M
        CPU: 22ms
     CGroup: /system.slice/nginx.service
             ├─1235 "nginx: master process /usr/sbin/nginx -g daemon on; master_process on;"
             └─1236 "nginx: worker process"

Jun 06 10:00:00 server systemd[1]: Starting A high performance web server...
Jun 06 10:00:00 server systemd[1]: Started A high performance web server.
"""

_STATUS_ACTIVE_DISABLED = """\
● nginx.service - A high performance web server and a reverse proxy server
     Loaded: loaded (/lib/systemd/system/nginx.service; disabled; vendor preset: enabled)
     Active: active (running) since Mon 2026-06-06 10:00:00 UTC; 5min ago
   Main PID: 1235 (nginx)

Jun 06 10:00:00 server systemd[1]: Started A high performance web server.
"""

_STATUS_INACTIVE = """\
● nginx.service - A high performance web server and a reverse proxy server
     Loaded: loaded (/lib/systemd/system/nginx.service; disabled; vendor preset: enabled)
     Active: inactive (dead)

Jun 06 09:55:00 server systemd[1]: nginx.service: Failed to start — see 'journalctl -xe' for details.
Jun 06 09:55:00 server systemd[1]: Failed to start A high performance web server.
"""

_SS_WITH_80 = """\
Netid  State   Recv-Q  Send-Q  Local Address:Port  Peer Address:Port Process
tcp    LISTEN  0       511     0.0.0.0:80           0.0.0.0:*         users:(("nginx",pid=1235,fd=6))
tcp    LISTEN  0       511     [::]:80              [::]:*            users:(("nginx",pid=1235,fd=7))
tcp    LISTEN  0       128     0.0.0.0:22           0.0.0.0:*         users:(("sshd",pid=789,fd=3))
"""

_SS_WITHOUT_80 = """\
Netid  State   Recv-Q  Send-Q  Local Address:Port  Peer Address:Port Process
tcp    LISTEN  0       128     0.0.0.0:22           0.0.0.0:*         users:(("sshd",pid=789,fd=3))
"""

_JOURNALCTL_ACTIVE = """\
Jun 06 10:00:00 server systemd[1]: Starting A high performance web server and a reverse proxy server...
Jun 06 10:00:00 server nginx[1234]: nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
Jun 06 10:00:00 server nginx[1234]: nginx: configuration file /etc/nginx/nginx.conf test is successful
Jun 06 10:00:00 server systemd[1]: Started A high performance web server and a reverse proxy server.
"""

_JOURNALCTL_INACTIVE = """\
Jun 06 09:55:00 server systemd[1]: nginx.service: Failed to start A high performance web server and a reverse proxy server.
Jun 06 09:55:00 server systemd[1]: nginx.service: Unit entered failed state.
Jun 06 09:55:00 server systemd[1]: nginx.service: Failed with result 'exit-code'.
"""

_CURL_ACTIVE = """\
  % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
                                 Dload  Upload   Total   Spent    Left  Speed
100   612  100   612    0     0   612k      0 --:--:-- --:--:-- --:--:--  612k
<!DOCTYPE html>
<html>
<head><title>Welcome to nginx!</title></head>
<body>
<h1>Welcome to nginx!</h1>
<p>If you see this page, the nginx web server is successfully installed.</p>
</body>
</html>
"""

_CURL_INACTIVE_STDERR = "curl: (7) Failed to connect to localhost port 80 after 0 ms: Connection refused"


class MockSSHRunner:
    """Deterministic fake SSH runner for the nginx-stopped-and-disabled incident.

    This is for offline/demo/testing only; it does NOT connect anywhere.

    Internal state
    --------------
    active : bool  — whether nginx is currently running
    enabled : bool — whether nginx is enabled to start on boot
    """

    def __init__(self) -> None:
        self.active: bool = False
        self.enabled: bool = False

    # ------------------------------------------------------------------
    # Connection lifecycle (no-op for mock)
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """No-op — mock does not open any real connection."""

    async def close(self) -> None:
        """No-op — mock has nothing to close."""

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "MockSSHRunner":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Core: run
    # ------------------------------------------------------------------

    async def run(
        self,
        command: str,
        *,
        timeout: Optional[int] = None,  # noqa: ARG002 — ignored in mock
    ) -> CommandResult:
        """Return a canned CommandResult matching the mock nginx incident state.

        Defense in depth: re-checks classify() and raises HardBlockedError on
        HARD_BLOCK before anything else (SPEC §0.2, §5).
        """
        # --- Defense-in-depth safety re-check (mirrors real SSHRunner) ---
        classification = classify(command)
        if classification.verdict == Verdict.HARD_BLOCK:
            raise HardBlockedError(
                f"Command refused (hard block): {classification.reason} "
                f"[rule: {classification.matched_rule}]"
            )

        # Strip leading sudo for matching purposes
        stripped = _strip_sudo(command)

        return self._dispatch(stripped, command)

    # ------------------------------------------------------------------
    # Dispatch table
    # ------------------------------------------------------------------

    def _dispatch(self, stripped: str, original: str) -> CommandResult:  # noqa: PLR0911
        """Match the stripped command against known patterns and return result."""

        # --- systemctl mutations (check before reads so enable --now matches) ---

        # enable --now (start + enable atomically)
        if re.search(r"\bsystemctl\b.*\benable\b.*--now\b.*\bnginx\b", stripped) or \
           re.search(r"\bsystemctl\b.*--now\b.*\benable\b.*\bnginx\b", stripped):
            self.active = True
            self.enabled = True
            return _ok("", "")

        # start
        if re.search(r"\bsystemctl\b.*\bstart\b.*\bnginx\b", stripped):
            self.active = True
            return _ok("", "")

        # restart
        if re.search(r"\bsystemctl\b.*\brestart\b.*\bnginx\b", stripped):
            self.active = True
            return _ok("", "")

        # reload (service reload, not daemon-reload)
        if re.search(r"\bsystemctl\b.*\breload\b.*\bnginx\b", stripped):
            # only works when active
            if self.active:
                return _ok("", "")
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr="Failed to reload nginx.service: Unit not active.",
                duration_ms=12,
            )

        # stop
        if re.search(r"\bsystemctl\b.*\bstop\b.*\bnginx\b", stripped):
            self.active = False
            return _ok("", "")

        # disable --now (stop + disable atomically)
        if re.search(r"\bsystemctl\b.*\bdisable\b.*--now\b.*\bnginx\b", stripped) or \
           re.search(r"\bsystemctl\b.*--now\b.*\bdisable\b.*\bnginx\b", stripped):
            self.active = False
            self.enabled = False
            return _ok("", "")

        # enable (without --now)
        if re.search(r"\bsystemctl\b.*\benable\b.*\bnginx\b", stripped):
            self.enabled = True
            return _ok("Created symlink /etc/systemd/system/multi-user.target.wants/nginx.service.", "")

        # disable (without --now)
        if re.search(r"\bsystemctl\b.*\bdisable\b.*\bnginx\b", stripped):
            self.enabled = False
            return _ok("Removed /etc/systemd/system/multi-user.target.wants/nginx.service.", "")

        # daemon-reload
        if re.search(r"\bsystemctl\b.*\bdaemon-reload\b", stripped) or \
           re.search(r"\bdaemon-reload\b", stripped):
            return _ok("", "")

        # --- systemctl reads ---

        # is-active
        if re.search(r"\bsystemctl\b.*\bis-active\b.*\bnginx\b", stripped):
            if self.active:
                return _ok("active", "")
            return CommandResult(exit_code=3, stdout="inactive", stderr="", duration_ms=8)

        # is-enabled
        if re.search(r"\bsystemctl\b.*\bis-enabled\b.*\bnginx\b", stripped):
            if self.enabled:
                return _ok("enabled", "")
            return CommandResult(exit_code=1, stdout="disabled", stderr="", duration_ms=8)

        # status
        if re.search(r"\bsystemctl\b.*\bstatus\b.*\bnginx\b", stripped):
            if self.active and self.enabled:
                body = _STATUS_ACTIVE
            elif self.active and not self.enabled:
                body = _STATUS_ACTIVE_DISABLED
            else:
                body = _STATUS_INACTIVE
            exit_code = 0 if self.active else 3
            return CommandResult(exit_code=exit_code, stdout=body, stderr="", duration_ms=15)

        # --- ss / netstat ---
        if re.search(r"\bss\b", stripped) and re.search(r"-t", stripped):
            return _ok(_SS_WITH_80 if self.active else _SS_WITHOUT_80, "")

        if re.search(r"\bnetstat\b", stripped):
            return _ok(_SS_WITH_80 if self.active else _SS_WITHOUT_80, "")

        # --- journalctl for nginx ---
        if re.search(r"\bjournalctl\b.*\bnginx\b", stripped) or \
           re.search(r"\bjournalctl\b.*-u\b.*\bnginx\b", stripped):
            body = _JOURNALCTL_ACTIVE if self.active else _JOURNALCTL_INACTIVE
            return _ok(body, "")

        # --- nginx config test ---
        if re.search(r"\bnginx\b.*-[tT]\b", stripped) or \
           re.search(r"\bnginx\s+-[tT]\b", stripped):
            return _ok(
                "nginx: the configuration file /etc/nginx/nginx.conf syntax is ok\n"
                "nginx: configuration file /etc/nginx/nginx.conf test is successful",
                "",
            )

        # --- curl / wget to localhost ---
        if re.search(r"\bcurl\b", stripped) or re.search(r"\bwget\b", stripped):
            if re.search(r"(localhost|127\.0\.0\.1)", stripped):
                if self.active:
                    return _ok(_CURL_ACTIVE, "")
                return CommandResult(
                    exit_code=7,
                    stdout="",
                    stderr=_CURL_INACTIVE_STDERR,
                    duration_ms=5,
                )

        # --- Generic fallback: return success with empty output ---
        # This keeps the loop from breaking on exploratory read commands that
        # aren't explicitly modelled (ls, cat, df, id, etc.)
        return _ok("", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUDO_RE = re.compile(
    r"^sudo"
    r"(?:\s+-n)?"
    r"(?:\s+-E)?"
    r"(?:\s+-u\s+\S+)?"
    r"(?:\s+-n)?"
    r"(?:\s+-E)?"
    r"(?:\s+--)?"
    r"\s+"
)


def _strip_sudo(command: str) -> str:
    """Remove a leading sudo (with common flags) from the command string."""
    m = _SUDO_RE.match(command.strip())
    if m:
        return command.strip()[m.end():]
    return command.strip()


def _ok(stdout: str, stderr: str) -> CommandResult:
    """Return a successful CommandResult (exit_code=0)."""
    return CommandResult(exit_code=0, stdout=stdout, stderr=stderr, duration_ms=10)
