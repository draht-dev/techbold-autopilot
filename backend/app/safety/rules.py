"""Deterministic safety layer.

Two responsibilities, both deterministic (no LLM in the security boundary):

1. ``decide(command, auto_approve_reads)`` -> ALLOW / CONFIRM / DENY.
   - DENY  : a hard-fail from docs/scoring.md. Never runs, even if a human says yes.
   - CONFIRM: a mutation / unknown command. Requires explicit human approval.
   - ALLOW : a known read-only command (auto-runs only when the run allows it).

2. ``redact(text)`` -> scrub secrets from any command output before it is
   persisted, shown in the UI, or written into an activity.

The command is split into segments (``;`` ``&&`` ``||`` ``|`` ``&``) and the
worst category across segments wins. Deny always takes precedence.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
PROTECTED_PATHS = (
    "/",
    "/*",
    "/etc",
    "/var",
    "/var/log",
    "/var/lib",
    "/var/lib/postgresql",
    "/var/lib/mysql",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/boot",
    "/home",
    "/root",
    "/srv",
    "/dev",
    "/proc",
    "/sys",
)

SECRET_FILE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\.pem$",
        r"\.key$",
        r"\.p12$",
        r"\.pfx$",
        r"(^|/)id_rsa",
        r"(^|/)id_ed25519",
        r"(^|/)id_dsa",
        r"(^|/)id_ecdsa",
        r"(^|/)\.env",
        r"/etc/shadow",
        r"/etc/gshadow",
        r"/etc/ssl/private",
        r"\.ssh/",
        r"\.aws/credentials",
        r"(^|/)credentials(\.[a-z]+)?$",
        r"\.netrc$",
        r"\.pgpass$",
    )
]

# Commands that only read state. Anything not listed is treated as mutating.
READ_ONLY_COMMANDS = {
    "ls", "cat", "head", "tail", "less", "more", "wc", "stat", "file", "find",
    "grep", "egrep", "fgrep", "zgrep", "awk", "cut", "sort", "uniq", "tr", "nl",
    "uname", "hostname", "hostnamectl", "whoami", "id", "uptime", "date",
    "timedatectl", "locale", "df", "du", "free", "ps", "pstree", "lsblk", "lsof",
    "ss", "netstat", "ip", "ifconfig", "dig", "nslookup", "host", "getent",
    "dmesg", "vmstat", "iostat", "mpstat", "pwd", "echo", "printf", "which",
    "whereis", "type", "command", "test", "true", "false", "readlink", "realpath",
    "basename", "dirname", "md5sum", "sha1sum", "sha256sum", "cmp", "diff",
    "tac", "column", "tee", "xargs", "watch", "ulimit", "ldd", "ldconfig",
}

# Commands that read secrets when pointed at a secret file.
SECRET_READERS = {
    "cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "zgrep",
    "strings", "xxd", "od", "base64", "nl", "tac", "awk", "sed", "cp", "scp",
}

# Service-management units that must not be disabled/stopped without need.
SECURITY_UNITS = {"ufw", "firewalld", "auditd", "apparmor", "fail2ban", "ssh", "sshd"}

PREFIX_NOISE = {"sudo", "doas", "command", "nice", "ionice", "nohup", "time", "env"}


class Category(IntEnum):
    SAFE_READ = 0
    MUTATING = 1
    DANGEROUS = 2  # hard-fail


@dataclass
class Decision:
    action: str  # "ALLOW" | "CONFIRM" | "DENY"
    category: Category
    reason: str
    segment: str = ""


# --------------------------------------------------------------------------- #
# Command splitting
# --------------------------------------------------------------------------- #
_OPERATORS = {"|", "||", "&&", ";", "&", "\n", "|&"}


def split_segments(command: str) -> list[list[str]]:
    """Split a (possibly compound) command into argv-style segments.

    We only segment on the control operators ``; | &`` (and their runs like
    ``&&`` / ``||``). Redirections (``>``) are deliberately kept inside the
    segment so the dangerous-redirect checks can still see them.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";|&")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        # Unbalanced quotes etc. — fall back to a naive split so we still inspect.
        tokens = command.split()

    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in _OPERATORS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _strip_prefixes(argv: list[str]) -> list[str]:
    """Drop sudo/env/VAR=val prefixes to find the real command base."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "env":
            # Only a prefix when it actually wraps another command
            # (e.g. `env FOO=bar systemctl ...`); bare `env` dumps secrets.
            rest = argv[i + 1 :]
            wraps_command = any(not r.startswith("-") and "=" not in r for r in rest)
            if wraps_command:
                i += 1
                continue
            break
        if tok in PREFIX_NOISE:
            i += 1
            continue
        if "=" in tok and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            i += 1
            continue
        if tok.startswith("-"):  # flags for the prefix (e.g. sudo -n)
            i += 1
            continue
        break
    return argv[i:]


def _is_protected(path: str) -> bool:
    p = path.rstrip("/") or "/"
    if path in ("/", "/*"):
        return True
    for prot in PROTECTED_PATHS:
        pr = prot.rstrip("/") or "/"
        if p == pr or p == prot:
            return True
    return False


def _touches_protected(args: list[str]) -> bool:
    for a in args:
        if a.startswith("-"):
            continue
        if a in ("/", "/*") or _is_protected(a):
            return True
    return False


def _is_secret_path(path: str) -> bool:
    return any(p.search(path) for p in SECRET_FILE_PATTERNS)


_REDIRECT_RE = re.compile(r"^\d*[<>]")


def _is_redirect(token: str) -> bool:
    """True for shell redirection tokens like `2>/dev/null`, `>>file`, `&>`."""
    return bool(_REDIRECT_RE.match(token)) or token in ("&>", ">&")


# --------------------------------------------------------------------------- #
# Per-segment classification
# --------------------------------------------------------------------------- #
def classify_segment(argv: list[str]) -> Decision:
    real = _strip_prefixes(argv)
    if not real:
        return Decision("ALLOW", Category.SAFE_READ, "no-op")
    base = real[0]
    # Redirection tokens (e.g. `2>/dev/null`) are not real arguments; exclude them
    # from arg analysis but keep them in seg_text for the dangerous-redirect checks.
    args = [a for a in real[1:] if not _is_redirect(a)]
    seg_text = " ".join(argv)
    flags = "".join(a for a in args if a.startswith("-"))
    recursive = bool(re.search(r"-[a-z]*[rR]", flags)) or "--recursive" in args
    paths = [a for a in args if not a.startswith("-")]

    def deny(reason: str) -> Decision:
        return Decision("DENY", Category.DANGEROUS, reason, seg_text)

    def confirm(reason: str) -> Decision:
        return Decision("CONFIRM", Category.MUTATING, reason, seg_text)

    def safe(reason: str = "read-only") -> Decision:
        return Decision("ALLOW", Category.SAFE_READ, reason, seg_text)

    lowered = seg_text.lower()

    # --- Disk / device destruction ----------------------------------------- #
    if base.startswith("mkfs"):
        return deny("filesystem format (mkfs)")
    if base == "dd" and any(a.startswith("of=/dev/") for a in args):
        return deny("dd writing to a block device")
    if base in ("shred", "wipefs"):
        return deny(f"destructive wipe ({base})")

    # --- rm: recursive deletes of protected / critical paths --------------- #
    if base == "rm":
        if recursive and (_touches_protected(paths) or any(p in ("/", "/*") for p in paths)):
            return deny("recursive delete of a protected path")
        if any(_is_protected(p) for p in paths):
            return deny("delete of a protected system path")
        if any(p.startswith("/var/log") for p in paths):
            return deny("deleting logs (audit tampering)")
        if any("postgresql" in p or "mysql" in p or "/var/lib" in p for p in paths):
            return deny("deleting database data")
        if any(".bash_history" in p or "history" in p for p in paths):
            return deny("deleting shell history (audit tampering)")
        return confirm("file deletion")

    # --- chmod / chown: blanket permission changes ------------------------- #
    if base in ("chmod", "chown", "chgrp"):
        mode_777 = any(re.fullmatch(r"0?777", a) for a in args)
        if recursive and _touches_protected(paths):
            return deny(f"recursive {base} on a protected path")
        if mode_777 and (_touches_protected(paths) or recursive):
            return deny("chmod 777 on a protected/recursive path")
        return confirm(f"permission change ({base})")

    # --- redirection that truncates logs / devices ------------------------- #
    if ">" in seg_text:
        if re.search(r">\s*/var/log", seg_text):
            return deny("truncating logs (audit tampering)")
        if re.search(r">\s*/dev/sd", seg_text):
            return deny("overwriting a block device")

    if base == "truncate" and any(p.startswith("/var/log") for p in paths):
        return deny("truncating logs (audit tampering)")

    # --- history tampering -------------------------------------------------- #
    if base == "history" and ("-c" in args or "-w" in args):
        return deny("clearing shell history (audit tampering)")

    # --- firewall / security controls off ---------------------------------- #
    if base == "ufw" and "disable" in args:
        return deny("disabling the firewall (ufw)")
    if base == "iptables" and ("-F" in args or "--flush" in args):
        return deny("flushing firewall rules (iptables -F)")
    if base == "nft" and "flush" in args:
        return deny("flushing nftables ruleset")
    if base == "setenforce" and "0" in args:
        return deny("disabling SELinux enforcement")
    if base in ("aa-disable", "aa-complain"):
        return deny("disabling AppArmor enforcement")

    # --- DB destruction ----------------------------------------------------- #
    if base in ("dropdb",):
        return deny("dropping a database")
    if base == "mysqladmin" and "drop" in args:
        return deny("dropping a database")
    if base in ("psql", "mysql", "mariadb") and re.search(
        r"\b(drop\s+database|drop\s+table|truncate\s+table?|delete\s+from)\b", lowered
    ):
        return deny("destructive SQL (drop/truncate/delete)")

    # --- secret reads / exfiltration --------------------------------------- #
    if base in SECRET_READERS and any(_is_secret_path(p) for p in paths):
        return deny("reading a secret file")
    if base in ("env", "printenv") and not args:
        return confirm("dumping environment (may contain secrets)")

    # --- systemctl / service: stopping security units = deny --------------- #
    if base in ("systemctl", "service"):
        positional = [a for a in args if not a.startswith("-")]
        action = positional[0] if positional else ""
        units = [re.sub(r"\.service$", "", u) for u in positional[1:]]
        if action in ("stop", "disable", "mask", "kill") and any(u in SECURITY_UNITS for u in units):
            return deny(f"disabling a security/remote-access unit ({action})")
        read_actions = {
            "status", "is-active", "is-enabled", "is-failed", "show", "cat",
            "list-units", "list-unit-files", "list-dependencies", "list-timers",
            "list-sockets", "get-default", "",
        }
        if action in read_actions:
            return safe("systemctl read")
        return confirm(f"service management (systemctl {action})")

    # --- journalctl: rotating/vacuuming logs is tampering ------------------ #
    if base == "journalctl":
        if any(a.startswith("--vacuum") or a == "--rotate" for a in args):
            return deny("rotating/vacuuming logs (audit tampering)")
        return safe("journalctl read")

    # --- sed -i / tee / package installs are mutations --------------------- #
    if base == "sed" and ("-i" in flags or any(a.startswith("-i") for a in args)):
        return confirm("in-place edit (sed -i)")
    if base in ("apt", "apt-get", "aptitude", "dpkg", "snap", "yum", "dnf", "pip", "pip3", "npm"):
        if base in ("apt", "apt-get") and any(a in ("list", "show", "policy") for a in args):
            return safe("package query")
        if base == "dpkg" and ("-l" in args or "-L" in args or "-s" in args):
            return safe("package query")
        return confirm(f"package operation ({base})")

    # --- find with destructive actions ------------------------------------- #
    if base == "find" and ("-delete" in args or "-exec" in args or "-execdir" in args):
        return confirm("find with a destructive action")

    # --- sysctl write ------------------------------------------------------- #
    if base == "sysctl" and "-w" in args:
        return confirm("runtime kernel parameter change (sysctl -w)")

    # --- curl/wget: only auto-allow loopback validation -------------------- #
    if base in ("curl", "wget"):
        if re.search(r"(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)", seg_text):
            return safe("local HTTP probe")
        return confirm("network request")

    # --- process / mount control ------------------------------------------- #
    if base in ("kill", "pkill", "killall", "mount", "umount", "reboot",
                "shutdown", "init", "telinit", "crontab", "usermod", "useradd",
                "userdel", "passwd", "visudo", "ln", "mv", "cp", "tee", "tar",
                "unzip", "make", "git"):
        return confirm(f"state-changing command ({base})")

    # --- known read-only ---------------------------------------------------- #
    if base in READ_ONLY_COMMANDS:
        return safe()

    # --- unknown -> require approval --------------------------------------- #
    return confirm(f"unrecognised command ({base})")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{.*\|.*&.*\}\s*;\s*:")


def classify(command: str) -> Decision:
    """Return the worst-category decision across all segments of the command."""
    # Some catastrophic patterns do not survive tokenisation; check the raw text.
    if _FORK_BOMB.search(command) or ":|:&" in command.replace(" ", ""):
        return Decision("DENY", Category.DANGEROUS, "fork bomb", command)

    segments = split_segments(command)
    if not segments:
        return Decision("CONFIRM", Category.MUTATING, "empty command")
    worst: Optional[Decision] = None
    for argv in segments:
        d = classify_segment(argv)
        if worst is None or d.category > worst.category:
            worst = d
        if d.category == Category.DANGEROUS:
            break
    assert worst is not None
    return worst


def decide(command: str, auto_approve_reads: bool = True) -> Decision:
    """Resolve the final action given the run's auto-approve-reads setting."""
    d = classify(command)
    if d.category == Category.DANGEROUS:
        return Decision("DENY", d.category, d.reason, d.segment)
    if d.category == Category.SAFE_READ:
        if auto_approve_reads:
            return Decision("ALLOW", d.category, d.reason, d.segment)
        return Decision("CONFIRM", d.category, "read-only (manual confirm mode)", d.segment)
    return Decision("CONFIRM", d.category, d.reason, d.segment)


# --------------------------------------------------------------------------- #
# Secret redaction
# --------------------------------------------------------------------------- #
_REDACTION_PATTERNS = [
    re.compile(r"-----BEGIN [^-]+-----[\s\S]*?-----END [^-]+-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWT
    re.compile(r"(?i)\b(?:bearer|token)\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(
        r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|"
        r"secret[_-]?key|private[_-]?key|client[_-]?secret|auth[_-]?token)\b"
        r"\s*[:=]\s*\"?[^\s\"']+\"?"
    ),
    re.compile(r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s\"']+"),
    re.compile(r"\$[0-9a-z]\$[^\s:'\"]{8,}"),  # /etc/shadow style hashes
]

REDACTION_PLACEHOLDER = "[REDACTED]"


def redact(text: Optional[str]) -> tuple[str, int]:
    """Return (scrubbed_text, number_of_redactions)."""
    if not text:
        return text or "", 0
    count = 0

    def _sub(match: re.Match) -> str:
        nonlocal count
        count += 1
        full = match.group(0)
        # Keep the leading key (e.g. "password=") so logs stay readable.
        if "=" in full or ":" in full:
            sep = "=" if "=" in full else ":"
            head = full.split(sep, 1)[0]
            return f"{head}{sep} {REDACTION_PLACEHOLDER}"
        return REDACTION_PLACEHOLDER

    out = text
    for pat in _REDACTION_PATTERNS:
        out = pat.sub(_sub, out)
    return out, count
