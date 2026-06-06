"""Tests for SSH key-path resolution (multi-key support).

The competition ships one distinct key per incident VM under ``keys/`` (e.g.
``case1_key.pem`` … ``case5_key.pem``).  ``resolve_key_paths`` must turn a single
config value (file / directory / comma-list) into the full candidate list that
asyncssh tries, while never picking up ``*.pub`` or stray files.
"""
from __future__ import annotations

import os
from pathlib import Path

from app.ssh_runner import resolve_key_paths

REPO_ROOT = Path(__file__).resolve().parents[2]
KEYS_DIR = REPO_ROOT / "keys"


def test_single_file_path_returns_itself():
    assert resolve_key_paths("keys/case1_key.pem") == ["keys/case1_key.pem"]


def test_comma_separated_list_is_split():
    out = resolve_key_paths("keys/a.pem, keys/b.pem ,keys/c.pem")
    assert out == ["keys/a.pem", "keys/b.pem", "keys/c.pem"]


def test_list_input_is_normalised():
    assert resolve_key_paths(["k1.pem", " k2.pem "]) == ["k1.pem", "k2.pem"]


def test_directory_expands_to_all_pem_keys():
    out = resolve_key_paths(str(KEYS_DIR))
    bases = sorted(os.path.basename(p) for p in out)
    # All five case keys are discovered...
    assert bases == [f"case{i}_key.pem" for i in range(1, 6)]
    # ...and stray files are excluded.
    assert all(not b.endswith(".pub") for b in bases)
    assert ".gitkeep" not in bases
    assert ".DS_Store" not in bases


def test_directory_results_are_sorted_and_deduped():
    out = resolve_key_paths(str(KEYS_DIR))
    assert out == sorted(out)
    assert len(out) == len(set(out))


def test_nonexistent_path_falls_back_to_value():
    # A bad path must surface later as a clear connect/auth error, not vanish.
    assert resolve_key_paths("/no/such/key.pem") == ["/no/such/key.pem"]
