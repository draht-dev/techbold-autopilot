"""
rules.py — Rule tables for the safety classifier.

Data / tables only. No logic here — all logic lives in classifier.py.

Sections:
  SYSTEM_ROOTS        — protected filesystem paths (for deletion / recursive ops)
  DB_DATA_DIRS        — database data directory paths
  SECURITY_SERVICES   — systemd units that are security-critical
  HARD_BLOCK_PATTERNS — compiled regex patterns, each with a rule name and reason
  READ_ONLY_COMMANDS  — allowlisted command names (first real token)
  SYSTEMCTL_READ_SUBCOMMANDS — which systemctl subcommands are read-only
  IP_READ_SUBCOMMANDS — which ip subcommands are read-only
"""

import re
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Protected filesystem roots (for recursive deletion, chmod, chown)
# ---------------------------------------------------------------------------

# These are the exact paths that are hard-blocked when targeted recursively
# OR when chmod 777 is applied directly to them (even non-recursively).
SYSTEM_ROOTS = (
    "/",
    "/*",
    "~",
    "~/",
    "/etc",
    "/var",
    "/var/lib",
    "/var/lib/postgresql",
    "/var/lib/mysql",
    "/home",
    "/srv",
    "/boot",
    "/usr",
    "/lib",
    "/lib64",
    "/bin",
    "/sbin",
    "/root",
)

# DB data directories (any rm *targeting* these prefixes is hard-blocked)
DB_DATA_DIRS = (
    "/var/lib/postgresql",
    "/var/lib/mysql",
)

# Security / audit services: stop|disable|mask of any of these is hard-blocked
SECURITY_SERVICES = (
    "ufw",
    "firewalld",
    "nftables",
    "netfilter-persistent",
    "auditd",
    "apparmor",
)

# ---------------------------------------------------------------------------
# Helper: build a regex that matches a path as a standalone token.
# Matches the path optionally followed by / but NOT followed by more path
# components (so /var matches /var and /var/ but NOT /var/www/app).
# ---------------------------------------------------------------------------

def _path_token(path: str) -> str:
    """Return a regex fragment that matches `path` as a complete path token."""
    escaped = re.escape(path)
    if path in ("~", "~/", "/", "/*"):
        # These are exact matches only — allow whitespace, end-of-string, OR a quote
        # (so that bash -c "rm -rf /" with a trailing " is still caught).
        # Also accept a backslash (so escaped-quote forms like rm -rf \"/ are caught).
        return escaped + r'(?=\s|$|["\x27\\])'
    # Match path, optional trailing slash, but NOT followed by another path component
    # (to avoid matching /var when the target is /var/www/app)
    # Use a lookahead: path must end at whitespace, end-of-string, quote, backslash,
    # or a trailing slash that is itself followed by whitespace/end/quote/backslash.
    return escaped + r'(?:/(?=\s|$|["\x27\\])|(?=\s|$|["\x27\\]))'


# ---------------------------------------------------------------------------
# Build pattern sets for recursive system deletion
# ---------------------------------------------------------------------------

# Recursive flags for rm: -r, -R, -rf, -fr, -rRf, --recursive, --force, etc.
_RM_RECURSIVE_FLAGS = r"-[a-zA-Z]*[rR][a-zA-Z]*|-[a-zA-Z]*r[a-zA-Z]*|--recursive(?:\s+--force)?|--force(?:\s+--recursive)?"

# Paths that are hard-blocked when rm targets them recursively
_rm_recursive_paths = "|".join(_path_token(p) for p in SYSTEM_ROOTS)

# Also match rm on DB data dirs (recursive or not - any rm on these dirs is blocked)
_db_paths = "|".join(re.escape(p) for p in DB_DATA_DIRS)


# ---------------------------------------------------------------------------
# HARD_BLOCK_PATTERNS
# Each entry: (rule_name, compiled_pattern, reason_template)
# ---------------------------------------------------------------------------

class HardBlockRule(NamedTuple):
    name: str
    pattern: re.Pattern
    reason: str


def _c(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern:
    return re.compile(pattern, flags)


HARD_BLOCK_RULES: list[HardBlockRule] = [
    # -------------------------------------------------------------------
    # 1. Recursive system deletion
    # -------------------------------------------------------------------
    HardBlockRule(
        name="rm_recursive_system_root",
        pattern=_c(
            r"\brm\b"
            r"(?:\s+\S+)*?"               # any flags before the recursive one
            r"\s+(?:" + _RM_RECURSIVE_FLAGS + r")"
            r"(?:\s+\S+)*?"               # any flags after
            r"\s+(?:" + _rm_recursive_paths + r")",
        ),
        reason="Recursive rm targeting a protected system root",
    ),

    # -------------------------------------------------------------------
    # 2. World-open recursion (chmod -R 777 on system roots)
    # -------------------------------------------------------------------
    HardBlockRule(
        name="chmod_recursive_world_open_system_root",
        pattern=_c(
            r"\bchmod\b"
            r"(?:\s+\S+)*?"
            r"\s+(?:-R\b|--recursive\b)"
            r"(?:\s+\S+)*?"
            r"\s+(?:0?777|a\+rwx)"
            r"(?:\s+(?:" + "|".join(_path_token(p) for p in SYSTEM_ROOTS) + r"))?",
        ),
        reason="Recursive world-open chmod on system root",
    ),
    # chmod -R 777 /path_root  — mode before path
    HardBlockRule(
        name="chmod_recursive_world_open_system_root_v2",
        pattern=_c(
            r"\bchmod\b"
            r"(?:\s+\S+)*?"
            r"\s+(?:-R\b|--recursive\b)"
            r"\s+(?:0?777|a\+rwx)"
            r"\s+(?:" + "|".join(_path_token(p) for p in SYSTEM_ROOTS) + r")",
        ),
        reason="Recursive world-open chmod on system root",
    ),
    # chmod 777 directly on system root (non-recursive is also blocked for system roots)
    HardBlockRule(
        name="chmod_world_open_system_root_direct",
        pattern=_c(
            r"\bchmod\b"
            r"(?:\s+(?!-R\b|--recursive\b)\S+)*?"
            r"\s+(?:0?777|a\+rwx)"
            r"\s+(?:" + "|".join(_path_token(p) for p in SYSTEM_ROOTS) + r")",
        ),
        reason="World-open chmod directly on system root",
    ),

    # -------------------------------------------------------------------
    # 3. Broad recursive chown/chgrp on system roots
    # -------------------------------------------------------------------
    HardBlockRule(
        name="chown_recursive_system_root",
        # chown -R [flags] owner /path  (flag -R may come before or after owner)
        pattern=_c(
            r"\bchown\b"
            r"(?:\s+\S+)*?"
            r"\s+-R\b"
            r"(?:\s+\S+)*?"
            r"\s+(?:" + "|".join(_path_token(p) for p in SYSTEM_ROOTS) + r")",
        ),
        reason="Recursive chown on system root",
    ),
    HardBlockRule(
        name="chown_recursive_system_root_flag_first",
        # chown -R owner /path
        pattern=_c(
            r"\bchown\b"
            r"\s+-R"
            r"\s+\S+"    # owner:group
            r"\s+(?:" + "|".join(_path_token(p) for p in SYSTEM_ROOTS) + r")",
        ),
        reason="Recursive chown on system root",
    ),
    HardBlockRule(
        name="chgrp_recursive_system_root",
        # chgrp -R group /path
        pattern=_c(
            r"\bchgrp\b"
            r"(?:\s+\S+)*?"
            r"\s+-R\b"
            r"(?:\s+\S+)*?"
            r"\s+(?:" + "|".join(_path_token(p) for p in SYSTEM_ROOTS) + r")",
        ),
        reason="Recursive chgrp on system root",
    ),
    HardBlockRule(
        name="chgrp_recursive_system_root_flag_first",
        # chgrp -R group /path
        pattern=_c(
            r"\bchgrp\b"
            r"\s+-R"
            r"\s+\S+"    # group
            r"\s+(?:" + "|".join(_path_token(p) for p in SYSTEM_ROOTS) + r")",
        ),
        reason="Recursive chgrp on system root",
    ),

    # -------------------------------------------------------------------
    # 4. DB destruction — SQL patterns inside quoted arguments
    # -------------------------------------------------------------------
    HardBlockRule(
        name="db_drop_database",
        pattern=_c(r"\bDROP\s+DATABASE\b"),
        reason="DROP DATABASE detected",
    ),
    HardBlockRule(
        name="db_drop_table",
        pattern=_c(r"\bDROP\s+TABLE\b"),
        reason="DROP TABLE detected",
    ),
    HardBlockRule(
        name="db_truncate",
        pattern=_c(r"\bTRUNCATE\b(?:\s+TABLE)?\s+\w"),
        reason="TRUNCATE TABLE detected",
    ),
    HardBlockRule(
        name="dropdb_command",
        pattern=_c(r"\bdropdb\b"),
        reason="dropdb command destroys a database",
    ),
    HardBlockRule(
        name="mysqladmin_drop",
        pattern=_c(r"\bmysqladmin\b.*\bdrop\b"),
        reason="mysqladmin drop destroys a database",
    ),
    # DELETE FROM without WHERE — no WHERE clause at all (whitespace/end after table)
    HardBlockRule(
        name="db_delete_without_where",
        pattern=_c(r"\bDELETE\s+FROM\s+\S+\s*(?:;|$|\"|')"),
        reason="DELETE FROM without WHERE clause could delete all rows",
    ),
    # rm on DB data directories (any rm, not just recursive)
    HardBlockRule(
        name="rm_db_data_dir",
        pattern=_c(
            r"\brm\b(?:\s+\S+)*\s+(?:" +
            "|".join(re.escape(p) + r"(?:/\S*)?" for p in DB_DATA_DIRS) +
            r")",
        ),
        reason="rm targeting a database data directory",
    ),

    # -------------------------------------------------------------------
    # 5. Disabling security controls
    # -------------------------------------------------------------------
    HardBlockRule(
        name="ufw_disable",
        pattern=_c(r"\bufw\s+disable\b"),
        reason="Disabling ufw firewall",
    ),
    HardBlockRule(
        name="iptables_flush",
        pattern=_c(r"\bip6?tables\b.*\s-[FX]\b"),
        reason="Flushing iptables rules",
    ),
    HardBlockRule(
        name="nft_flush_ruleset",
        pattern=_c(r"\bnft\s+flush\s+ruleset\b"),
        reason="Flushing nft ruleset",
    ),
    HardBlockRule(
        name="systemctl_disable_security_service",
        pattern=_c(
            r"\bsystemctl\b"
            r"(?:\s+\S+)*?"
            r"\s+(?:stop|disable|mask)"
            r"(?:\s+--now)?"
            r"(?:\s+\S+)*?"
            r"\s+(?:" + "|".join(re.escape(s) for s in SECURITY_SERVICES) + r")\b",
        ),
        reason="Disabling/stopping a security service",
    ),
    HardBlockRule(
        name="setenforce_0",
        pattern=_c(r"\bsetenforce\s+0\b"),
        reason="Disabling SELinux enforcement",
    ),
    HardBlockRule(
        name="aa_disable",
        pattern=_c(r"\baa-disable\b"),
        reason="Disabling AppArmor profile",
    ),

    # -------------------------------------------------------------------
    # 6. Secret exposure
    # -------------------------------------------------------------------
    HardBlockRule(
        name="read_etc_shadow",
        pattern=_c(
            r"\b(?:cat|less|head|tail|more|view|tee|grep|awk|sed|strings|xxd|od|base64)\b"
            r".*\s/etc/shadow\b"
        ),
        reason="Reading /etc/shadow exposes password hashes",
    ),
    # Private key files: id_rsa, id_ed25519, id_dsa, id_ecdsa, *.pem, *.key
    # Matches: ~/. ssh/id_*, /.ssh/id_*, /home/user/.ssh/id_*, /root/.ssh/id_*,
    #          any absolute path ending in .ssh/id_* or bare id_* key names.
    HardBlockRule(
        name="read_private_key_file",
        pattern=_c(
            r"\b(?:cat|less|head|tail|more|view|tee|grep|strings)\b"
            r".*\s(?:"
            r"~/\.ssh/id_(?:rsa|ed25519|dsa|ecdsa)\b"
            r"|[^\s]*/\.?ssh/id_(?:rsa|ed25519|dsa|ecdsa)\b"
            r"|[^\s]*\.pem\b"
            r"|[^\s]*\.key\b"
            r")",
        ),
        reason="Reading a private key file",
    ),
    # Secret-reading tools (xxd, od, base64, openssl, dd) applied to private key files
    # or /etc/shadow.
    HardBlockRule(
        name="secret_tool_on_key_or_shadow",
        pattern=_c(
            r"\b(?:xxd|od|base64|strings)\b"
            r".*\s(?:"
            r"~/\.ssh/id_(?:rsa|ed25519|dsa|ecdsa)\b"
            r"|[^\s]*/\.?ssh/id_(?:rsa|ed25519|dsa|ecdsa)\b"
            r"|[^\s]*\.pem\b"
            r"|[^\s]*\.key\b"
            r"|/etc/shadow\b"
            r")",
        ),
        reason="Using a binary-dump tool on a private key file or /etc/shadow",
    ),
    HardBlockRule(
        name="openssl_read_key",
        pattern=_c(
            r"\bopenssl\b.*\s-in\s+[^\s]*(?:\.pem|\.key|id_(?:rsa|ed25519|dsa|ecdsa))\b"
        ),
        reason="openssl reading a private key file",
    ),
    # dd if=<key file> — reading a private key via dd
    HardBlockRule(
        name="dd_read_key_or_shadow",
        pattern=_c(
            r"\bdd\b.*\bif=(?:"
            r"[^\s]*/\.?ssh/id_(?:rsa|ed25519|dsa|ecdsa)\b"
            r"|[^\s]*\.pem\b"
            r"|[^\s]*\.key\b"
            r"|/etc/shadow\b"
            r")"
        ),
        reason="dd reading a private key file or /etc/shadow",
    ),
    # env/printenv redirected to file
    HardBlockRule(
        name="env_redirect_to_file",
        pattern=_c(r"\b(?:env|printenv)\b.*>"),
        reason="Redirecting env/printenv output to a file exposes secrets",
    ),

    # -------------------------------------------------------------------
    # 7. Hiding actions
    # -------------------------------------------------------------------
    HardBlockRule(
        name="history_clear",
        pattern=_c(r"\bhistory\s+-c\b"),
        reason="Clearing shell history hides past actions",
    ),
    HardBlockRule(
        name="rm_bash_history",
        pattern=_c(r"\brm\b.*\s~/\.bash_history\b"),
        reason="Deleting bash history hides past actions",
    ),
    HardBlockRule(
        name="truncate_bash_history",
        pattern=_c(r"\btruncate\b.*\s~/\.bash_history\b"),
        reason="Truncating bash history hides past actions",
    ),
    HardBlockRule(
        name="redirect_empty_bash_history",
        pattern=_c(r"(?:>\s*~/\.bash_history\b|:\s*>\s*~/\.bash_history\b)"),
        reason="Emptying bash history hides past actions",
    ),
    HardBlockRule(
        name="rm_var_log",
        pattern=_c(r"\brm\b(?:\s+\S+)*\s+/var/log/"),
        reason="Deleting log files hides system activity",
    ),
    HardBlockRule(
        name="truncate_var_log",
        pattern=_c(r"\btruncate\b.*\s/var/log/"),
        reason="Truncating log files hides system activity",
    ),
    HardBlockRule(
        name="redirect_empty_var_log",
        pattern=_c(r"(?:>\s*/var/log/|:\s*>\s*/var/log/)"),
        reason="Emptying log files hides system activity",
    ),
    HardBlockRule(
        name="journalctl_vacuum",
        pattern=_c(r"\bjournalctl\b.*--vacuum-(?:time|size|files)"),
        reason="journalctl vacuum clears log data",
    ),

    # -------------------------------------------------------------------
    # 8. Priv-esc to dodge DB perms
    # -------------------------------------------------------------------
    HardBlockRule(
        name="user_root_in_service",
        pattern=_c(r"User=root"),
        reason="Setting User=root in a service/config bypasses permission controls",
    ),
    HardBlockRule(
        name="runuser_root",
        pattern=_c(r"\brunuser\b.*-u\s+root\b"),
        reason="Running a process as root via runuser bypasses permission controls",
    ),

    # -------------------------------------------------------------------
    # 9. Catastrophic
    # -------------------------------------------------------------------
    HardBlockRule(
        name="fork_bomb",
        # Classic fork bomb: :(){ :|:& };: and common variants
        pattern=_c(r":\(\)\{.*:\|:&.*\}"),
        reason="Fork bomb detected",
    ),
    HardBlockRule(
        name="dd_to_device",
        pattern=_c(r"\bdd\b.*\bof=/dev/(?:sd[a-z]|nvme\d|disk\d|vd[a-z])"),
        reason="dd to a block device would overwrite disk data",
    ),
    HardBlockRule(
        name="redirect_to_device",
        pattern=_c(r">\s*/dev/(?:sd[a-z]|nvme\d|disk\d|vd[a-z])"),
        reason="Redirecting to a block device would corrupt disk data",
    ),
    HardBlockRule(
        name="mkfs_device",
        pattern=_c(r"\bmkfs(?:\.\w+)?\b.*\s/dev/"),
        reason="mkfs on a device formats and destroys data",
    ),
    HardBlockRule(
        name="wipefs",
        pattern=_c(r"\bwipefs\b"),
        reason="wipefs destroys filesystem signatures",
    ),
    HardBlockRule(
        name="shred_device",
        pattern=_c(r"\bshred\b.*\s/dev/"),
        reason="shred on a block device destroys data",
    ),

    # -------------------------------------------------------------------
    # 10. find <system-root> -delete or -exec rm → catastrophic deletion
    # Hard-block when the target path is a system root AND the find has
    # -delete or -exec/-execdir rm.  Deep app paths are NOT hard-blocked
    # (they stay NEEDS_APPROVAL via the existing find allowlist logic).
    # -------------------------------------------------------------------
    HardBlockRule(
        name="find_system_root_delete",
        pattern=_c(
            r"\bfind\b"
            r"\s+(?:" + "|".join(_path_token(p) for p in SYSTEM_ROOTS) + r")"
            r"(?:\s+\S+)*?"
            r"\s+-delete\b",
        ),
        reason="find -delete targeting a system root would destroy critical files",
    ),
    HardBlockRule(
        name="find_system_root_exec_rm",
        pattern=_c(
            r"\bfind\b"
            r"\s+(?:" + "|".join(_path_token(p) for p in SYSTEM_ROOTS) + r")"
            r"(?:\s+\S+)*?"
            r"\s+-exec(?:dir)?\b(?:\s+\S+)*?\s+\brm\b",
        ),
        reason="find -exec rm targeting a system root would destroy critical files",
    ),

    # -------------------------------------------------------------------
    # 11. Secret copy-out: cp/tar/scp/rsync/dd/install of /etc/shadow
    #     or SSH private keys → HARD_BLOCK
    # -------------------------------------------------------------------
    HardBlockRule(
        name="copy_shadow_or_key",
        pattern=_c(
            r"\b(?:cp|scp|rsync|install)\b"
            r"(?:\s+\S+)*?"
            r"\s+(?:"
            r"/etc/shadow\b"
            r"|[^\s]*/\.?ssh/id_(?:rsa|ed25519|dsa|ecdsa)\b"
            r"|[^\s]*\.pem\b"
            r"|[^\s]*\.key\b"
            r")",
        ),
        reason="Copying /etc/shadow or a private key file exposes secrets",
    ),
    HardBlockRule(
        name="tar_shadow_or_key",
        pattern=_c(
            r"\btar\b"
            r"(?:\s+\S+)*?"
            r"\s+(?:"
            r"/etc/shadow\b"
            r"|[^\s]*/\.?ssh/id_(?:rsa|ed25519|dsa|ecdsa)\b"
            r"|[^\s]*\.pem\b"
            r"|[^\s]*\.key\b"
            r")",
        ),
        reason="tar archiving /etc/shadow or a private key file exposes secrets",
    ),
    HardBlockRule(
        name="dd_copy_shadow_or_key",
        # dd if=<secret> → copy of secret data; dd of=<secret> is also dangerous
        # but covered by existing dd_read_key_or_shadow rule.
        # This rule catches dd if=<secret> explicitly for the copy-out scenario.
        pattern=_c(
            r"\bdd\b"
            r"(?:\s+\S+)*?"
            r"\bif=(?:"
            r"/etc/shadow\b"
            r"|[^\s]*/\.?ssh/id_(?:rsa|ed25519|dsa|ecdsa)\b"
            r"|[^\s]*\.pem\b"
            r"|[^\s]*\.key\b"
            r")",
        ),
        reason="dd reading /etc/shadow or a private key file exposes secrets",
    ),

    # -------------------------------------------------------------------
    # 12. chmod -R symbolic world-writable on system roots → HARD_BLOCK
    #     Extends existing numeric 777/a+rwx rules to catch:
    #       chmod -R o+w /etc, chmod -R a+w /var, chmod -R o=rwx /srv,
    #       chmod -R go+rwx /home
    #     Only system roots are hard-blocked; deep app paths stay NEEDS_APPROVAL.
    # -------------------------------------------------------------------
    HardBlockRule(
        name="chmod_recursive_symbolic_world_write_system_root",
        # Handles: chmod -R <mode> <path> and chmod <mode> -R <path>
        # Path MUST be a system root (not optional) to avoid over-blocking deep paths.
        pattern=_c(
            r"\bchmod\b"
            r"(?:\s+\S+)*?"
            r"\s+(?:-R\b|--recursive\b)"
            r"(?:\s+\S+)*?"
            r"\s+(?:[uago]*[go]+\+[rwx]*w[rwx]*|[uago]*o[+=][rwx]*w[rwx]*|a\+w\b)"
            r"\s+(?:" + "|".join(_path_token(p) for p in SYSTEM_ROOTS) + r")",
        ),
        reason="Recursive symbolic world-writable chmod on system root",
    ),
    HardBlockRule(
        name="chmod_recursive_symbolic_world_write_system_root_v2",
        # Canonical form: chmod -R <mode> <path>
        pattern=_c(
            r"\bchmod\b"
            r"(?:\s+\S+)*?"
            r"\s+(?:-R\b|--recursive\b)"
            r"\s+(?:[uago]*[go]+\+[rwx]*w[rwx]*|[uago]*o[+=][rwx]*w[rwx]*|a\+w\b)"
            r"\s+(?:" + "|".join(_path_token(p) for p in SYSTEM_ROOTS) + r")",
        ),
        reason="Recursive symbolic world-writable chmod on system root",
    ),
]


# ---------------------------------------------------------------------------
# READ_ONLY allowlist
# ---------------------------------------------------------------------------

# Commands where the base name alone is always read-only
# (some have additional conditions checked in classifier.py)
READ_ONLY_COMMANDS: frozenset[str] = frozenset({
    "cat",
    "head",
    "tail",
    "less",
    "ls",
    "stat",
    "file",
    "readlink",
    "grep",
    "egrep",
    "fgrep",
    "zgrep",
    "journalctl",
    "ss",
    "netstat",
    "ps",
    "pgrep",
    "top",
    "htop",
    "df",
    "du",
    "free",
    "uptime",
    "dmesg",
    "id",
    "whoami",
    "groups",
    "getent",
    "ping",
    "nginx",
    "apache2ctl",
    "apachectl",
    "sshd",
    "named-checkconf",
    "findmnt",
    "mount",
    "getcap",
    "crontab",
    "date",
    "hostnamectl",
    "lsblk",
    "uname",
    "lscpu",
    "lsof",
    "systemd-analyze",
    "dpkg",
    "dpkg-query",
    "rpm",
    "apt",
    "which",
    "whereis",
    "type",
    "env",
    "printenv",
    "getsebool",
    "sestatus",
    "timedatectl",
    "resolvectl",
    "dig",
    "nslookup",
    "host",
    "vmstat",
    "iostat",
    "mpstat",
    "sar",
    "ulimit",
    "find",
    "systemctl",
    "ip",
    "sysctl",
    "curl",
    "wget",
})

# systemctl subcommands that are read-only
SYSTEMCTL_READ_SUBCOMMANDS: frozenset[str] = frozenset({
    "status",
    "is-active",
    "is-enabled",
    "is-failed",
    "list-units",
    "list-unit-files",
    "list-dependencies",
    "cat",
    "show",
    "get-default",
})

# ip subcommands that are read-only (aliases covered in classifier.py)
IP_READ_SUBCOMMANDS: frozenset[str] = frozenset({
    "a",
    "addr",
    "address",
    "r",
    "route",
    "l",
    "link",
    "n",
    "neigh",
    "neighbour",
    "neighbor",
})

# ---------------------------------------------------------------------------
# Shell invocations that carry a -c payload (for recursive classification)
# ---------------------------------------------------------------------------

# Shells that accept -c <cmd> and should have their payload recursively checked.
SHELL_C_INVOCATIONS: frozenset[str] = frozenset({
    "bash",
    "sh",
    "dash",
    "zsh",
    "ash",
    "su",
})

# ---------------------------------------------------------------------------
# File-reading commands whose output may expose secret content
# (used in classifier.py for CRITICAL-2b secret-ish path downgrade)
# ---------------------------------------------------------------------------

# Commands that produce plaintext file output
FILE_READING_COMMANDS: frozenset[str] = frozenset({
    "cat",
    "head",
    "tail",
    "less",
    "grep",
    "strings",
    "xxd",
    "od",
    "base64",
})

# Paths that are explicitly ALLOWED despite matching secret-ish patterns.
# These stay READ_ONLY.
SECRET_PATH_ALLOWLIST: tuple[str, ...] = (
    "/etc/passwd",
    "/etc/passwd-",
)

# Compiled regex: matches paths that are "secret-ish" and should be downgraded
# to NEEDS_APPROVAL when read by a file-reading command.
# Patterns (case-sensitive where meaningful):
#   - /run/secrets/ prefix (Docker/K8s/systemd-creds canonical mount)
#   - ends with .env or contains /.env or .env. (dotenv files)
#   - "secrets" (singular or plural) appearing as or within a path component
#     including _/-/. neighbours (e.g. .secrets, secrets.yaml, api_secret.conf)
#   - "credential(s)" in a path component
#   - "passwd" or "pass(wd|word)" within a component (including db_password,
#     database_password, postgres-password, api_secret), but NOT /etc/passwd
#     (handled by the allowlist in SECRET_PATH_ALLOWLIST)
#   - .ssh/ directory
#   - .aws/credentials
#   - .pgpass, .my.cnf, .netrc
#   - a file literally named "token"
#   - /proc/<pid>/environ (pid = digits or "self") — process environment
SECRET_ISH_PATH_RE: re.Pattern = re.compile(
    r"(?:"
    r"/run/secrets/"                              # Docker/K8s/systemd-creds mount
    r"|(?:^|/)\.env(?:\.|$)"                      # .env, .env.something at path boundary
    r"|\.env$"                                    # ends with .env
    r"|(?:^|/)\.?secrets?(?:[/_\-.]|$)"           # secret/secrets/.secrets/secrets.yaml/api_secret.conf
    r"|[_\-]secrets?\b"                           # _secret, -secret, _secrets, db_secrets
    r"|(?:^|/)\.?credentials?(?:/|$|\.)"          # "credential(s)" or ".credential(s)"
    r"|(?:^|/)passwd(?!-?\s*$)"                   # "passwd" in path but NOT /etc/passwd alone
    r"|(?:^|/)pass(?:wd|word)(?:[/_\-.]|$|\b)"   # password/passwd as component start
    r"|[_\-]pass(?:wd|word)\b"                    # _password, -password, db_password, database_password
    r"|/\.ssh/"                                   # .ssh/ directory
    r"|\.aws/credentials"                         # AWS credentials
    r"|\.pgpass$"                                 # pgpass
    r"|\.my\.cnf$"                                # MySQL client config
    r"|\.netrc$"                                  # netrc
    r"|(?:^|/)token$"                             # a file literally named "token"
    r"|/proc/[^/\s]+/environ\b"                   # /proc/<pid>/environ (process env secrets)
    r")",
    re.IGNORECASE,
)
