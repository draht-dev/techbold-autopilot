"""
redaction.py — Secret redaction utility.

Public API:
    redact(text: str) -> str
    make_redacting_log_filter() -> logging.Filter

Patterns applied in safe order (most structured first to avoid
double-processing):
  1. Private key blocks (-----BEGIN ... PRIVATE KEY-----)
  2. DB connection URIs with credentials
  3. JWT tokens
  4. Long standalone tokens (OpenAI sk-, AKIA, ghp_, xox*, long hex/base64)
  5. Env-style KEY=value assignments
  6. /etc/shadow hash fields
  7. PHOENIX_API_TOKEN literal value

Never import heavy dependencies — pure stdlib: re, logging.
"""

from __future__ import annotations

import logging
import re


# ---------------------------------------------------------------------------
# Substitution marker
# ---------------------------------------------------------------------------

def _tag(reason: str) -> str:
    return f"«redacted:{reason}»"


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# 1. Private key blocks (multi-line)
_RE_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    re.MULTILINE,
)

# 2. DB connection URIs with credentials
# Matches scheme://user:pass@host... — redacts user:pass@ but keeps the rest
# Also handles empty username form :password@ (as used by Redis)
_RE_DB_URI = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb|redis|amqp)://)"
    r"(?:[^:@\s]*:[^@\s]*@)"  # user:pass@ or :pass@ (empty user)
    r"([^\s\"']+)",           # rest (host/path)
    re.IGNORECASE,
)

# 3. JWT tokens: eyJ...<base64>.<base64>.<base64-or-empty>
_RE_JWT = re.compile(
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]*)?",
)

# 4a. OpenAI / Anthropic style keys: sk-[optional prefix-]32+ chars
_RE_SK_KEY = re.compile(
    r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b",
)

# 4b. AWS access key ID: AKIA[A-Z0-9]{16}
_RE_AWS_KEY = re.compile(
    r"\bAKIA[A-Z0-9]{16}\b",
)

# 4c. GitHub PAT: ghp_<alphanumeric 30+> (total token usually 36+ chars)
_RE_GH_PAT = re.compile(
    r"\bghp_[A-Za-z0-9]{30,}\b",
)

# 4d. Slack tokens: xox[baprs]-...
_RE_SLACK = re.compile(
    r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b",
)

# 4e. Long hex tokens: 32+ hex chars (but NOT in the middle of normal text like PIDs)
# Must be surrounded by non-hex chars or start/end of string
_RE_LONG_HEX = re.compile(
    r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{32,}(?![A-Fa-f0-9])",
)

# 4f. Long base64-ish tokens: 40+ chars of base64 alphabet that aren't ordinary words
# We require them to look "random" — mixed case + numbers or +/= chars
_RE_LONG_B64 = re.compile(
    r"\b(?=[A-Za-z0-9+/]{40,}(?:={0,2})\b)"
    r"(?=[A-Z][a-z]|[a-z][A-Z]|[A-Za-z]\d|\d[A-Za-z])"  # mixed
    r"[A-Za-z0-9+/]{40,}={0,2}\b",
)

# 5. Env-style assignments: KEY=value or KEY="value" or KEY='value'
# KEY must look like an env var name (uppercase with _)
# We match: PASSWORD, PASSWD, PWD, SECRET, TOKEN, API_KEY,
#           anything ending in _KEY, _TOKEN, _SECRET, _PASSWORD, _PASSWD,
#           PHOENIX_API_TOKEN specifically
_ENV_KEY_PATTERN = (
    r"(?:PASSWORD|PASSWD|PWD|SECRET|TOKEN|API_KEY"
    r"|[A-Z][A-Z0-9_]*_KEY"
    r"|[A-Z][A-Z0-9_]*_TOKEN"
    r"|[A-Z][A-Z0-9_]*_SECRET"
    r"|[A-Z][A-Z0-9_]*_PASSWORD"
    r"|[A-Z][A-Z0-9_]*_PASSWD"
    r"|PHOENIX_API_TOKEN"
    r")"
)
_RE_ENV_ASSIGNMENT = re.compile(
    _ENV_KEY_PATTERN + r'=(\"[^\"]*\"|\'[^\']*\'|[^\s\"\';&|]+)',
    re.IGNORECASE,
)

# 6. /etc/shadow hash fields: user:$N$...:rest
# Format: username:HASH:lastchg:... where HASH starts with $
_RE_SHADOW_LINE = re.compile(
    r"^([^:]+):(\$[^\s:]+):(.*)$",
    re.MULTILINE,
)

# 7. PHOENIX_API_TOKEN bare value patterns (belt-and-suspenders, caught by #5 too)
# Already handled by ENV_ASSIGNMENT above, but add a standalone catch
# for cases like: Authorization: Bearer <token>
_RE_BEARER_TOKEN = re.compile(
    r"(Authorization:\s*Bearer\s+)([A-Za-z0-9\-_\.]{20,})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Redaction pipeline
# ---------------------------------------------------------------------------

def redact(text: str) -> str:
    """
    Redact secret values from `text`, keeping structure and keys intact.

    Patterns applied in order:
      1. Private key blocks
      2. DB URIs with credentials
      3. JWT tokens
      4. Standalone tokens (sk-, AKIA, ghp_, xox*, long hex)
      5. Env-style KEY=value assignments
      6. /etc/shadow hash fields
      7. Bearer tokens
    """
    # 1. Private key blocks
    text = _RE_PRIVATE_KEY_BLOCK.sub(_tag("private-key"), text)

    # 2. DB URIs — keep scheme + @host/db, redact user:pass
    def _redact_db_uri(m: re.Match) -> str:
        scheme = m.group(1)
        rest = m.group(2)
        return f"{scheme}{_tag('credentials')}@{rest}"

    text = _RE_DB_URI.sub(_redact_db_uri, text)

    # 3. JWT tokens
    text = _RE_JWT.sub(_tag("jwt"), text)

    # 4a. OpenAI/Anthropic sk- keys
    text = _RE_SK_KEY.sub(_tag("api-key"), text)

    # 4b. AWS access key IDs
    text = _RE_AWS_KEY.sub(_tag("aws-key"), text)

    # 4c. GitHub PATs
    text = _RE_GH_PAT.sub(_tag("github-token"), text)

    # 4d. Slack tokens
    text = _RE_SLACK.sub(_tag("slack-token"), text)

    # 4e. Long hex tokens
    text = _RE_LONG_HEX.sub(_tag("token"), text)

    # 5. Env-style assignments — keep KEY=, redact value
    def _redact_env(m: re.Match) -> str:
        key_eq = m.group(0)[: m.group(0).index("=") + 1]
        key_lower = key_eq.rstrip("=").lower()
        if "password" in key_lower or "passwd" in key_lower or "pwd" in key_lower:
            return key_eq + _tag("password")
        if "secret" in key_lower:
            return key_eq + _tag("secret")
        if "token" in key_lower:
            return key_eq + _tag("token")
        return key_eq + _tag("secret")

    text = _RE_ENV_ASSIGNMENT.sub(_redact_env, text)

    # 6. /etc/shadow hash lines
    def _redact_shadow(m: re.Match) -> str:
        username = m.group(1)
        rest = m.group(3)
        return f"{username}:{_tag('password-hash')}:{rest}"

    text = _RE_SHADOW_LINE.sub(_redact_shadow, text)

    # 7. Bearer tokens
    def _redact_bearer(m: re.Match) -> str:
        return m.group(1) + _tag("token")

    text = _RE_BEARER_TOKEN.sub(_redact_bearer, text)

    return text


# ---------------------------------------------------------------------------
# Logging filter
# ---------------------------------------------------------------------------

class _RedactingFilter(logging.Filter):
    """A logging.Filter that redacts secrets from log record messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact the formatted message
        record.msg = redact(record.getMessage())
        record.args = ()  # args already formatted into msg
        return True


def make_redacting_log_filter() -> logging.Filter:
    """Return a logging.Filter that redacts secrets from log records."""
    return _RedactingFilter()
