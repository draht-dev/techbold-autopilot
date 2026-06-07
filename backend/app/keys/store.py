"""Store for SSH private keys uploaded from the dev menu.

Primary storage is the local keys directory (the parent of
``SSH_PRIVATE_KEY_PATH``) — that is what :func:`Settings.ssh_key_path_for_ticket`
and the SSH runner read. When an S3 bucket is configured the same bytes are
mirrored to S3 so a freshly-started replica (or a wiped container) can pull the
keys back; without a bucket the S3 calls are skipped and everything runs off
local disk.

boto3 is imported lazily so the dependency is only needed when S3 is actually
configured — a missing boto3 degrades to "local only" instead of crashing.
"""
from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

# Only accept private-key-looking filenames; never let an upload escape the dir.
# Empty suffix allows extensionless keys (e.g. ``id_rsa``); ``.txt`` etc. are out.
_ALLOWED_SUFFIXES = {".pem", ".key", ""}


@dataclass
class SavedKey:
    name: str
    bytes: int
    s3: bool  # True if also mirrored to S3


def _safe_name(filename: str) -> str:
    """Reduce an uploaded filename to a bare basename (no path traversal)."""
    return os.path.basename(filename or "").strip()


class KeyStore:
    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._keys_dir = settings.keys_dir

    # ------------------------------------------------------------------ #
    def _s3(self):
        """Return an (client, bucket, prefix) tuple, or ``None`` if unavailable."""
        if not self._settings.s3_keys_configured:
            return None
        try:
            import boto3  # noqa: PLC0415 — lazy: only needed when S3 is configured
        except ImportError:
            logger.warning("S3 bucket configured but boto3 is not installed; skipping S3 mirror")
            return None
        kwargs = {}
        if self._settings.aws_region:
            kwargs["region_name"] = self._settings.aws_region
        client = boto3.client("s3", **kwargs)
        return client, self._settings.s3_keys_bucket, self._settings.s3_keys_prefix

    def _s3_key(self, prefix: str, name: str) -> str:
        return f"{prefix.rstrip('/')}/{name}" if prefix else name

    # ------------------------------------------------------------------ #
    def save_uploads(self, files: Iterable[tuple[str, bytes]]) -> list[SavedKey]:
        """Persist uploaded keys to the local keys dir (and S3 if configured).

        ``files`` is an iterable of ``(filename, content)``. Returns one
        :class:`SavedKey` per accepted file. Raises ``ValueError`` if a filename
        is unusable so the API can return a 400.
        """
        self._keys_dir.mkdir(parents=True, exist_ok=True)
        s3 = self._s3()
        saved: list[SavedKey] = []
        for filename, content in files:
            name = _safe_name(filename)
            if not name:
                raise ValueError("a key file is missing a name")
            if Path(name).suffix.lower() not in _ALLOWED_SUFFIXES:
                raise ValueError(f"{name!r}: only .pem/.key files are accepted")
            if not content:
                raise ValueError(f"{name!r}: file is empty")

            dest = self._keys_dir / name
            dest.write_bytes(content)
            # Private keys must be 0600 or asyncssh/ssh refuse them.
            dest.chmod(stat.S_IRUSR | stat.S_IWUSR)

            mirrored = False
            if s3 is not None:
                client, bucket, prefix = s3
                try:
                    client.put_object(
                        Bucket=bucket, Key=self._s3_key(prefix, name), Body=content
                    )
                    mirrored = True
                except Exception as exc:  # noqa: BLE001 — S3 is best-effort
                    logger.warning("S3 mirror of %s failed: %s", name, exc)

            saved.append(SavedKey(name=name, bytes=len(content), s3=mirrored))
            logger.info("stored SSH key %s (%d bytes, s3=%s)", name, len(content), mirrored)
        return saved

    # ------------------------------------------------------------------ #
    def fetch_from_s3(self, name: str) -> bool:
        """Download a single key from S3 into the local keys dir if present.

        Returns True if the key now exists locally. No-op (returns False) when
        S3 is not configured/available.
        """
        name = _safe_name(name)
        if not name:
            return False
        s3 = self._s3()
        if s3 is None:
            return False
        client, bucket, prefix = s3
        dest = self._keys_dir / name
        try:
            obj = client.get_object(Bucket=bucket, Key=self._s3_key(prefix, name))
            body = obj["Body"].read()
        except Exception as exc:  # noqa: BLE001 — missing key / network: just fall back
            logger.info("S3 fetch of %s failed: %s", name, exc)
            return False
        self._keys_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        dest.chmod(stat.S_IRUSR | stat.S_IWUSR)
        logger.info("pulled SSH key %s from S3 (%d bytes)", name, len(body))
        return True
