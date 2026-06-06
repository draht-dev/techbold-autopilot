"""
classifier.py — Safety classifier for proposed shell commands.

Public API:
    classify(command: str) -> Classification

Order of evaluation (SPEC §4, mandatory):
  1. Split command into segments on |, &&, ||, ;, and newlines.
  2. Classify each segment individually.
  3. Return the MOST RESTRICTIVE verdict across all segments:
       HARD_BLOCK > NEEDS_APPROVAL > READ_ONLY

Per segment:
  a. Strip leading sudo (and its flags: -n, -u user, -E) → classify underlying.
  b. Strip common wrappers: env VAR=x, nice, ionice, timeout <dur>, nohup.
  c. Parse the real command with shlex (fall back if unbalanced quotes).
  d. Test HARD_BLOCK patterns (rules.HARD_BLOCK_RULES) against the full
     *raw* segment text (after sudo stripping but before shlex).
  e. If no hard-block, test the READ_ONLY allowlist by command name.
  f. Else → NEEDS_APPROVAL.

Never import from models.py, config, asyncssh, httpx, or any heavy module.
Pure stdlib: re, shlex, logging, dataclasses, enum.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum

from app.safety.rules import (
    FILE_READING_COMMANDS,
    HARD_BLOCK_RULES,
    IP_READ_SUBCOMMANDS,
    READ_ONLY_COMMANDS,
    SECRET_ISH_PATH_RE,
    SECRET_PATH_ALLOWLIST,
    SYSTEMCTL_READ_SUBCOMMANDS,
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Verdict(str, Enum):
    READ_ONLY = "read_only"
    NEEDS_APPROVAL = "needs_approval"
    HARD_BLOCK = "hard_block"


@dataclass
class Classification:
    verdict: Verdict
    reason: str
    matched_rule: str | None


# ---------------------------------------------------------------------------
# Verdict ordering (higher index = more restrictive)
# ---------------------------------------------------------------------------

_VERDICT_ORDER = {
    Verdict.READ_ONLY: 0,
    Verdict.NEEDS_APPROVAL: 1,
    Verdict.HARD_BLOCK: 2,
}


def _more_restrictive(a: Classification, b: Classification) -> Classification:
    if _VERDICT_ORDER[b.verdict] > _VERDICT_ORDER[a.verdict]:
        return b
    return a


# ---------------------------------------------------------------------------
# Segment splitting on shell operators
# ---------------------------------------------------------------------------

# Split on: | (but not ||), &&, ||, ;, and newlines
# We preserve order but just need each piece for separate classification.
_SEGMENT_SPLIT = re.compile(r"\|\||&&|[|;&\n]")


def _split_segments(command: str) -> list[str]:
    """Split a compound shell command into individual segments."""
    parts = _SEGMENT_SPLIT.split(command)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# Sudo stripping
# ---------------------------------------------------------------------------

# Match: sudo [ -n ] [ -E ] [ -i ] [ -u user ] [ -- ] ...
# -i is also stripped so the underlying command is correctly classified.
_SUDO_RE = re.compile(
    r"^sudo"
    r"(?:\s+-n)?"
    r"(?:\s+-E)?"
    r"(?:\s+-i)?"
    r"(?:\s+-u\s+\S+)?"
    r"(?:\s+-n)?"
    r"(?:\s+-E)?"
    r"(?:\s+--)?"
    r"\s+"
)


def _strip_sudo(segment: str) -> str:
    """Remove a leading sudo (with common flags) from a segment."""
    m = _SUDO_RE.match(segment)
    if m:
        return segment[m.end():]
    return segment


# ---------------------------------------------------------------------------
# Wrapper stripping: env VAR=x, nice, ionice, timeout <N>, nohup
# ---------------------------------------------------------------------------

_WRAPPER_RE = re.compile(
    r"^(?:"
    r"(?:env\s+(?:[A-Z_][A-Z_0-9]*=\S*\s+)+)"  # env VAR=x VAR2=y ...
    r"|(?:nice(?:\s+-n\s*\d+)?)\s+"
    r"|(?:ionice(?:\s+-c\s*\d+(?:\s+-n\s*\d+)?)?)\s+"
    r"|(?:timeout\s+\S+)\s+"
    r"|(?:nohup)\s+"
    r")"
)


def _strip_wrappers(segment: str) -> str:
    """Repeatedly strip leading wrappers until none remain."""
    while True:
        m = _WRAPPER_RE.match(segment)
        if not m:
            break
        segment = segment[m.end():]
    return segment


# ---------------------------------------------------------------------------
# Path normalisation: strip trailing /. and /.. so /etc/. → /etc,
# and collapse repeated leading slashes //etc → /etc, /// → /
# ---------------------------------------------------------------------------

_TRAILING_DOT_RE = re.compile(r"/\.\.?(?=\s|$|[\"'])")
# Repeated leading slashes: //+ followed by a non-slash (//etc → /etc)
# or repeated slashes forming the root alone (///, ////,  → /)
_MULTI_LEADING_SLASH_RE = re.compile(r"((?<=\s)|(?<=^)|(?<=[\"'(`;]))/{2,}(?=[^/\s]|$)")
_ROOT_MULTI_SLASH_RE = re.compile(r"((?<=\s)|(?<=^)|(?<=[\"'(`;]))/{2,}(?=\s|$|[\"'(`;\\)])")


def _normalise_paths(text: str) -> str:
    """
    Normalise path tokens in a command string:
      1. Collapse repeated leading slashes: //etc → /etc, /// → /
      2. Strip trailing /. and /.. suffixes so /etc/. → /etc

    Applied before hard-block matching to prevent double-slash bypasses.
    """
    # Step 1: collapse //something → /something and /// (or more) → /
    # We target whitespace- (or start/quote/paren) -bounded path tokens
    text = _ROOT_MULTI_SLASH_RE.sub("/", text)
    text = _MULTI_LEADING_SLASH_RE.sub("/", text)

    # Step 2: strip trailing /. and /..
    for _ in range(4):
        new_text = _TRAILING_DOT_RE.sub("", text)
        if new_text == text:
            break
        text = new_text
    return text


# ---------------------------------------------------------------------------
# Command substitution extraction: $(...) and `...`
# ---------------------------------------------------------------------------

# Matches $(...)  — non-greedy, handles single level of nesting by tracking parens
_DOLLAR_SUBST_RE = re.compile(r"\$\(")
_BACKTICK_SUBST_RE = re.compile(r"`([^`]*)`")


def _extract_command_substitutions(text: str) -> list[str]:
    """
    Extract command bodies from $(...) and `...` substitutions in `text`.

    Returns a list of inner command strings.  Handles nested $(...)
    by tracking parenthesis depth.  On parse trouble (unbalanced etc.)
    returns whatever was collected without crashing.
    """
    results: list[str] = []

    # --- $(...) extraction with depth tracking ---
    i = 0
    while i < len(text):
        m = _DOLLAR_SUBST_RE.search(text, i)
        if not m:
            break
        start = m.end()  # position after the opening $(
        depth = 1
        j = start
        while j < len(text) and depth > 0:
            c = text[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            j += 1
        if depth == 0:
            body = text[start : j - 1]  # content between $( and matching )
            results.append(body)
            i = j
        else:
            # Unbalanced — skip past the $( we found and continue
            i = start

    # --- backtick extraction ---
    for bm in _BACKTICK_SUBST_RE.finditer(text):
        results.append(bm.group(1))

    return results


# ---------------------------------------------------------------------------
# Shell -c payload: extract inner command from bash -c "..." / sh -c '...'
# Uses shlex.split for robust handling of backslash-escaped inner quotes.
# ---------------------------------------------------------------------------

# Shells that can carry a -c payload
_SHELL_C_NAMES = frozenset({
    "bash", "sh", "dash", "zsh", "ash",
    # login-shell variants (the first token after splitting)
    "su",
})

# Regex used only as a fast pre-filter: is this string a shell invocation at all?
_SHELL_C_PREFILTER = re.compile(
    r"^(?:bash|sh|dash|zsh|ash|su)\b",
    re.IGNORECASE,
)


def _extract_shell_c_payload(text: str) -> str | None:
    """
    If `text` looks like `bash -c "..."` or `sh -c '...'` (or similar),
    return the inner command string (correctly unescaped by shlex).
    Returns None if no match or if shlex cannot parse the input.

    Uses shlex.split so that backslash-escaped inner quotes such as
    bash -c "rm -rf \\"/etc\\""  are handled correctly:
      shlex.split('bash -c "rm -rf \\"/etc\\""')
      → ['bash', '-c', 'rm -rf "/etc"']
    and the inner payload 'rm -rf "/etc"' is then recursively classified.
    """
    stripped = text.strip()
    if not _SHELL_C_PREFILTER.match(stripped):
        return None

    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return None

    if not tokens:
        return None

    # Normalise the shell name (strip path prefix if any, e.g. /bin/bash → bash)
    shell_name = tokens[0].rsplit("/", 1)[-1].lower()
    # Strip login flags: bash -l / bash --login / bash -i (these don't change -c semantics)
    # Strip su flags: su -, su -l, su - username, su - -c ... etc.
    idx = 1
    while idx < len(tokens) and tokens[idx] in ("-l", "--login", "-i", "--interactive", "-"):
        idx += 1
    # For `su`, also skip an optional username token before -c
    if shell_name == "su":
        # su [-] [username] -c cmd  — the username is a non-flag token before -c
        if idx < len(tokens) and not tokens[idx].startswith("-"):
            # peek: if the NEXT token after this one is -c, this one is a username → skip
            if idx + 1 < len(tokens) and tokens[idx + 1] == "-c":
                idx += 1

    # Now skip any other flags until we see -c
    while idx < len(tokens) and tokens[idx] != "-c":
        idx += 1

    # tokens[idx] must be "-c" and tokens[idx+1] is the payload
    if idx >= len(tokens) or tokens[idx] != "-c":
        return None  # No -c flag found
    payload_idx = idx + 1
    if payload_idx >= len(tokens):
        return None  # -c with no argument

    return tokens[payload_idx]


# ---------------------------------------------------------------------------
# Hard-block check: run against the full (stripped) segment text
# ---------------------------------------------------------------------------


def _check_hard_block(segment_text: str) -> Classification | None:
    """Return a HARD_BLOCK Classification if any rule matches, else None."""
    for rule in HARD_BLOCK_RULES:
        if rule.pattern.search(segment_text):
            return Classification(
                verdict=Verdict.HARD_BLOCK,
                reason=rule.reason,
                matched_rule=rule.name,
            )
    return None


# ---------------------------------------------------------------------------
# Read-only allowlist check
# ---------------------------------------------------------------------------


def _check_read_only(segment_text: str, tokens: list[str]) -> Classification | None:
    """
    Return a READ_ONLY Classification if the command is in the allowlist
    and meets any additional constraints. Return None if it doesn't qualify.
    """
    if not tokens:
        return None

    cmd = tokens[0]
    rest = tokens[1:]

    if cmd not in READ_ONLY_COMMANDS:
        return None

    # ----- Special-case commands with extra constraints -----

    # find: only read-only if no -delete, -exec, -execdir, -ok
    if cmd == "find":
        for t in rest:
            if t in ("-delete", "-exec", "-execdir", "-ok"):
                return None  # → NEEDS_APPROVAL
        return Classification(
            verdict=Verdict.READ_ONLY,
            reason="find without -delete/-exec is read-only",
            matched_rule="allowlist:find",
        )

    # systemctl: only specific read subcommands
    if cmd == "systemctl":
        # Extract the subcommand: skip flags (--no-pager, etc.)
        sub = _get_subcommand(rest)
        if sub in SYSTEMCTL_READ_SUBCOMMANDS:
            return Classification(
                verdict=Verdict.READ_ONLY,
                reason=f"systemctl {sub} is a read-only query",
                matched_rule="allowlist:systemctl",
            )
        return None  # → NEEDS_APPROVAL

    # ip: only read subcommands
    if cmd == "ip":
        sub = _get_subcommand(rest)
        if sub in IP_READ_SUBCOMMANDS:
            # Make sure no mutating action follows (add, del, set, flush)
            if not _ip_is_mutating(rest):
                return Classification(
                    verdict=Verdict.READ_ONLY,
                    reason=f"ip {sub} is a read-only query",
                    matched_rule="allowlist:ip",
                )
        return None  # → NEEDS_APPROVAL

    # sysctl: read-only for sysctl <name> or -a, but NOT -w
    if cmd == "sysctl":
        if "-w" in rest:
            return None  # → NEEDS_APPROVAL
        return Classification(
            verdict=Verdict.READ_ONLY,
            reason="sysctl read query",
            matched_rule="allowlist:sysctl",
        )

    # mount: only read-only with no arguments
    if cmd == "mount":
        if rest:
            return None  # → NEEDS_APPROVAL
        return Classification(
            verdict=Verdict.READ_ONLY,
            reason="mount with no arguments lists mounts",
            matched_rule="allowlist:mount",
        )

    # crontab: only -l (list) is read-only
    if cmd == "crontab":
        if "-l" in rest:
            return Classification(
                verdict=Verdict.READ_ONLY,
                reason="crontab -l lists crontab",
                matched_rule="allowlist:crontab",
            )
        return None

    # nginx: only -t or -T
    if cmd == "nginx":
        if rest and rest[0] in ("-t", "-T"):
            return Classification(
                verdict=Verdict.READ_ONLY,
                reason="nginx config test",
                matched_rule="allowlist:nginx",
            )
        return None

    # apachectl / apache2ctl: configtest or -t
    if cmd in ("apachectl", "apache2ctl"):
        if rest and rest[0] in ("configtest", "-t"):
            return Classification(
                verdict=Verdict.READ_ONLY,
                reason="apache config test",
                matched_rule="allowlist:apachectl",
            )
        return None

    # sshd: only -t or -T
    if cmd == "sshd":
        if rest and rest[0] in ("-t", "-T"):
            return Classification(
                verdict=Verdict.READ_ONLY,
                reason="sshd config test",
                matched_rule="allowlist:sshd",
            )
        return None

    # dpkg: only -l, -s, or dpkg-query
    if cmd == "dpkg":
        if rest and rest[0] in ("-l", "-s"):
            return Classification(
                verdict=Verdict.READ_ONLY,
                reason="dpkg list/status query",
                matched_rule="allowlist:dpkg",
            )
        return None

    # rpm: only -q (query)
    if cmd == "rpm":
        if rest and rest[0].startswith("-q"):
            return Classification(
                verdict=Verdict.READ_ONLY,
                reason="rpm query",
                matched_rule="allowlist:rpm",
            )
        return None

    # apt: only list subcommand
    if cmd == "apt":
        if rest and rest[0] == "list":
            return Classification(
                verdict=Verdict.READ_ONLY,
                reason="apt list query",
                matched_rule="allowlist:apt",
            )
        return None

    # curl: read-only only for GET/HEAD against localhost/127.0.0.1/::1
    if cmd in ("curl", "wget"):
        return _classify_curl_wget(cmd, rest, segment_text)

    # env/printenv: only when NOT redirected (redirect is caught by hard-block rules)
    if cmd in ("env", "printenv"):
        # If we got here, hard-block didn't fire, so no redirect
        return Classification(
            verdict=Verdict.READ_ONLY,
            reason=f"{cmd} bare listing (output redacted downstream)",
            matched_rule="allowlist:env",
        )

    # All other allowlisted commands pass as READ_ONLY
    return Classification(
        verdict=Verdict.READ_ONLY,
        reason=f"{cmd} is on the read-only allowlist",
        matched_rule=f"allowlist:{cmd}",
    )


def _get_subcommand(tokens: list[str]) -> str:
    """Extract the first non-flag token from a list (the subcommand)."""
    for t in tokens:
        if not t.startswith("-"):
            return t
    return ""


def _ip_is_mutating(tokens: list[str]) -> bool:
    """Return True if the ip command is mutating (add, del, set, flush, etc.)."""
    mutating_words = {"add", "del", "delete", "set", "flush", "change", "replace", "append"}
    for t in tokens:
        if t.lower() in mutating_words:
            return True
    return False


def _check_secret_ish_path(tokens: list[str]) -> Classification | None:
    """
    If the command is a file-reading tool AND any argument matches a secret-ish
    path pattern (and is not an explicitly allowed path), return NEEDS_APPROVAL.
    Returns None if no secret-ish path is found (caller continues normal flow).
    """
    if not tokens:
        return None
    cmd = tokens[0]
    if cmd not in FILE_READING_COMMANDS:
        return None
    for arg in tokens[1:]:
        # Skip flags
        if arg.startswith("-"):
            continue
        # Check allowlist first
        if arg in SECRET_PATH_ALLOWLIST:
            continue
        if SECRET_ISH_PATH_RE.search(arg):
            return Classification(
                verdict=Verdict.NEEDS_APPROVAL,
                reason=f"Reading a potentially secret file '{arg}' requires approval",
                matched_rule="secret_ish_path",
            )
    return None


def _classify_curl_wget(cmd: str, rest: list[str], segment_text: str) -> Classification | None:
    """
    curl/wget is READ_ONLY only when:
      - No explicit non-GET method (-X POST/PUT/DELETE, --data/-d, --upload-file)
      - Target host is localhost, 127.0.0.1, or [::1]
    """
    combined = " ".join(rest)

    # Non-GET indicators
    # Note: -d may appear at the start of `combined` (no leading space), so use \b
    mutating_flags = re.search(
        r"(?:-X\s*(?:POST|PUT|DELETE|PATCH)|"
        r"--data(?:-raw|-urlencode|-binary)?(?:\s|=)|"
        r"(?:^|\s)-d(?:\s|$)|"
        r"--upload-file(?:\s|=)|"
        r"--request\s+(?:POST|PUT|DELETE|PATCH))",
        combined, re.IGNORECASE,
    )
    if mutating_flags:
        return None  # → NEEDS_APPROVAL

    # Target URL must be localhost / 127.0.0.1 / ::1
    local_url = re.search(
        r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:/\S*)?",
        combined, re.IGNORECASE,
    )
    if local_url:
        return Classification(
            verdict=Verdict.READ_ONLY,
            reason=f"{cmd} GET/HEAD against localhost",
            matched_rule=f"allowlist:{cmd}",
        )

    return None  # → NEEDS_APPROVAL (external host or no URL)


# ---------------------------------------------------------------------------
# Per-segment classification
# ---------------------------------------------------------------------------


def _classify_segment(raw_segment: str, _depth: int = 0) -> Classification:
    """Classify a single (unsplit) shell command segment."""
    if not raw_segment.strip():
        return Classification(
            verdict=Verdict.READ_ONLY,
            reason="empty segment",
            matched_rule=None,
        )

    # Step 1: strip sudo (including -i flag)
    after_sudo = _strip_sudo(raw_segment.strip())

    # Step 2: strip wrappers
    after_wrappers = _strip_wrappers(after_sudo)

    # Step 3a: normalise trailing /. and /.. in paths (MED-1)
    normalised = _normalise_paths(after_wrappers)

    # Step 3b: hard-block check on normalised text
    hb = _check_hard_block(normalised)
    if hb:
        return hb

    # Step 3b2: command substitution recursive check (FIX 6)
    # Classify each $(...) and `...` body; fold in the most-restrictive verdict.
    if _depth < 3:
        subst_bodies = _extract_command_substitutions(normalised)
        for body in subst_bodies:
            body = body.strip()
            if not body:
                continue
            subst_result = _classify_segment(body, _depth=_depth + 1)
            if subst_result.verdict == Verdict.HARD_BLOCK:
                return subst_result
        # Note: READ_ONLY inner substitutions do NOT upgrade the outer verdict —
        # we only fold in HARD_BLOCK here.  NEEDS_APPROVAL inner substitutions
        # are not elevated either (the outer command's own verdict will stand).

    # Step 3c: shell -c recursive payload check (CRITICAL-1 Fix B)
    # Guard against infinite recursion with a depth limit of 3.
    # When the segment IS a shell -c invocation, we return the INNER verdict
    # directly (the shell wrapper itself has no independent dangerous action).
    if _depth < 3:
        inner = _extract_shell_c_payload(normalised)
        if inner is not None:
            # Recursively classify the inner command; fold verdict in.
            inner_result = _classify_segment(inner, _depth=_depth + 1)
            # For hard-block, return immediately.
            if inner_result.verdict == Verdict.HARD_BLOCK:
                return inner_result
            # For read-only inner payloads wrapped in bash -c, propagate the
            # inner verdict so that `bash -c "systemctl status nginx"` stays READ_ONLY.
            # We capture the inner result and will use it as the final verdict
            # if the outer command itself doesn't add any harder restriction.
            inner_downgrade: Classification | None = inner_result
            is_shell_c_invocation = True
        else:
            inner_downgrade = None
            is_shell_c_invocation = False
    else:
        inner_downgrade = None
        is_shell_c_invocation = False

    # Step 4: parse with shlex
    try:
        tokens = shlex.split(after_wrappers)
    except ValueError:
        # Unbalanced quotes or similar shlex error → conservative
        return Classification(
            verdict=Verdict.NEEDS_APPROVAL,
            reason="Could not parse command (unbalanced quotes or special syntax)",
            matched_rule=None,
        )

    if not tokens:
        return Classification(
            verdict=Verdict.READ_ONLY,
            reason="empty segment after stripping",
            matched_rule=None,
        )

    # Step 4b: hard-block check on the shlex-normalised form.
    # This catches commands where paths were quoted in the original text
    # (e.g. rm -rf "/" → tokens → "rm -rf /") but the regex patterns only
    # match unquoted paths.  The normalised form has all shell quoting removed.
    shlex_normalised = _normalise_paths(" ".join(tokens))
    if shlex_normalised != normalised:  # avoid re-running the same check twice
        hb2 = _check_hard_block(shlex_normalised)
        if hb2:
            return hb2

    # Step 5: read-only allowlist check
    ro = _check_read_only(after_wrappers, tokens)
    if ro:
        # Step 5a: secret-ish path downgrade (CRITICAL-2b)
        # If the command is a file-reading tool and an argument is a secret-ish path,
        # downgrade to NEEDS_APPROVAL instead of READ_ONLY.
        secret_downgrade = _check_secret_ish_path(tokens)
        if secret_downgrade:
            return secret_downgrade
        # Apply inner_downgrade if any (shell -c with read-only inner payload)
        if inner_downgrade and inner_downgrade.verdict != Verdict.READ_ONLY:
            return inner_downgrade
        return ro

    # Step 6: apply inner_downgrade if inner was harder than what we'd return
    if inner_downgrade and inner_downgrade.verdict == Verdict.HARD_BLOCK:
        return inner_downgrade

    # Step 7: For a pure shell -c invocation whose outer command is not on the
    # allowlist, propagate the inner verdict (so benign inner cmds stay READ_ONLY,
    # NEEDS_APPROVAL inner cmds stay NEEDS_APPROVAL rather than both collapsing
    # to the generic "bash not on allowlist" NEEDS_APPROVAL message).
    if is_shell_c_invocation and inner_downgrade is not None:
        return inner_downgrade

    # Step 8: everything else → NEEDS_APPROVAL
    return Classification(
        verdict=Verdict.NEEDS_APPROVAL,
        reason=f"Command '{tokens[0]}' is not on the read-only allowlist and requires approval",
        matched_rule=None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify(command: str) -> Classification:
    """
    Classify a shell command string.

    First checks hard-block patterns against the full raw command string
    (catches fork bombs, redirect-to-device etc. that contain shell operators).
    Then, if the whole command is a shell -c invocation, classifies the inner
    payload recursively (using shlex so that backslash-escaped inner quotes and
    shell operators inside the quoted payload are handled correctly — the inner
    payload is NOT split before recursion).
    Finally splits compound commands (pipes, &&, ||, ;, newlines) into segments,
    classifies each, and returns the most restrictive verdict.
    """
    # Pre-check: run hard-block patterns against the full raw command
    # (after path normalisation for /. and /.. suffixes).
    # This catches patterns like fork bombs and "> /dev/sda" that get split
    # by the segment splitter before per-segment checks can see them.
    normalised_command = _normalise_paths(command)
    full_hb = _check_hard_block(normalised_command)
    if full_hb:
        return full_hb

    # Pre-check: command substitution in the full raw command (FIX 6)
    # Classify each $(...) and `...` body in the raw command; if any is
    # HARD_BLOCK, short-circuit immediately.
    subst_bodies = _extract_command_substitutions(normalised_command)
    for body in subst_bodies:
        body = body.strip()
        if not body:
            continue
        subst_result = _classify_segment(body, _depth=0)
        if subst_result.verdict == Verdict.HARD_BLOCK:
            return subst_result

    # Pre-check for shell -c wrappers: extract the payload using shlex BEFORE
    # splitting on shell operators.  This prevents _split_segments from breaking
    # compound operators (&&, ||, ;) that appear inside the quoted -c payload.
    # Example: bash -c "echo hi && rm -rf \"/var\""
    #   → shlex extracts inner: echo hi && rm -rf "/var"
    #   → recursively classify the inner compound command → HARD_BLOCK
    inner_payload = _extract_shell_c_payload(command.strip())
    if inner_payload is not None:
        inner_result = classify(inner_payload)
        if inner_result.verdict == Verdict.HARD_BLOCK:
            return inner_result
        # For non-HARD_BLOCK inner payloads we fall through to the segment
        # splitter so that outer-level checks (secret paths etc.) still apply
        # through _classify_segment on the whole command.

    segments = _split_segments(command)

    if not segments:
        return Classification(
            verdict=Verdict.NEEDS_APPROVAL,
            reason="Empty or unparseable command",
            matched_rule=None,
        )

    result: Classification | None = None

    for seg in segments:
        seg_result = _classify_segment(seg)
        if result is None:
            result = seg_result
        else:
            result = _more_restrictive(result, seg_result)
        # Short-circuit: can't get worse than HARD_BLOCK
        if result.verdict == Verdict.HARD_BLOCK:
            return result

    # result is guaranteed non-None if segments is non-empty
    assert result is not None
    return result
