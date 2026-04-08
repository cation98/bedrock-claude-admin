"""Tests for secure-put, secure-get, and secure-cleanup CLI tools.

Tests are organised around the five required cases:
1. test_secure_put_missing_file       -- nonexistent file → exit 2
2. test_secure_put_missing_env        -- no POD_TOKEN → exit 1
3. test_secure_get_missing_env        -- no POD_TOKEN → exit 1
4. test_secure_cleanup_removes_expired -- expired marker file → directory removed
5. test_secure_cleanup_keeps_active    -- future marker → directory kept

CLI scripts live at container-image/secure-cli/ relative to the repo root.
They are invoked via subprocess so we test the actual script behaviour
(argument parsing, env-var validation, exit codes) without importing them
as modules.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

import pytest

# ---------------------------------------------------------------------------
# Helpers — locate script files relative to this test file
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]  # auth-gateway/tests/ → repo root
SECURE_CLI_DIR = REPO_ROOT / "container-image" / "secure-cli"

SECURE_PUT = str(SECURE_CLI_DIR / "secure-put")
SECURE_GET = str(SECURE_CLI_DIR / "secure-get")
SECURE_CLEANUP = str(SECURE_CLI_DIR / "secure-cleanup")


def _run(script: str, args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a CLI script with the given args and environment overlay."""
    base_env = {k: v for k, v in os.environ.items()}
    # Strip out any real pod-token variables so tests are isolated
    base_env.pop("SECURE_POD_TOKEN", None)
    base_env.pop("POD_NAME", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, script] + args,
        capture_output=True,
        text=True,
        env=base_env,
    )


def _load_cleanup_module() -> ModuleType:
    """Import secure-cleanup as a Python module (no .py extension)."""
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("secure_cleanup", SECURE_CLEANUP)
    spec = importlib.util.spec_from_loader("secure_cleanup", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. secure-put — missing file
# ---------------------------------------------------------------------------


def test_secure_put_missing_file():
    """Passing a non-existent file path should exit with code 2."""
    result = _run(
        SECURE_PUT,
        ["/tmp/this_file_does_not_exist_xyzzy.csv"],
        env={"SECURE_POD_TOKEN": "tok", "POD_NAME": "pod-test"},
    )

    assert result.returncode == 2, f"Expected exit 2, got {result.returncode}\nstderr: {result.stderr}"
    error = json.loads(result.stderr.strip())
    assert "error" in error
    assert "not found" in error["error"].lower() or "File not found" in error["error"]


# ---------------------------------------------------------------------------
# 2. secure-put — missing environment variables
# ---------------------------------------------------------------------------


def test_secure_put_missing_env(tmp_path):
    """No SECURE_POD_TOKEN / POD_NAME should exit with code 1."""
    # Create a real file so we get past the file-exists check
    target = tmp_path / "salary.csv"
    target.write_text("name,salary\nAlice,90000\n")

    result = _run(SECURE_PUT, [str(target)])  # no env vars → empty strings

    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}\nstderr: {result.stderr}"
    error = json.loads(result.stderr.strip())
    assert "error" in error
    assert "SECURE_POD_TOKEN" in error["error"]


def test_secure_put_missing_env_token_only(tmp_path):
    """Only POD_NAME set but no SECURE_POD_TOKEN should also exit 1."""
    target = tmp_path / "data.csv"
    target.write_text("col\n1\n")

    result = _run(SECURE_PUT, [str(target)], env={"POD_NAME": "pod-test"})

    assert result.returncode == 1
    error = json.loads(result.stderr.strip())
    assert "SECURE_POD_TOKEN" in error["error"]


# ---------------------------------------------------------------------------
# 3. secure-get — missing environment variables
# ---------------------------------------------------------------------------


def test_secure_get_missing_env():
    """No SECURE_POD_TOKEN / POD_NAME should exit with code 1."""
    result = _run(SECURE_GET, ["abc123def456abcd"])

    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}\nstderr: {result.stderr}"
    error = json.loads(result.stderr.strip())
    assert "error" in error
    assert "SECURE_POD_TOKEN" in error["error"]


def test_secure_get_missing_env_pod_name_only():
    """Only SECURE_POD_TOKEN set but no POD_NAME should exit 1."""
    result = _run(SECURE_GET, ["abc123def456abcd"], env={"SECURE_POD_TOKEN": "tok"})

    assert result.returncode == 1
    error = json.loads(result.stderr.strip())
    assert "SECURE_POD_TOKEN" in error["error"] or "POD_NAME" in error["error"]


# ---------------------------------------------------------------------------
# 4. secure-cleanup — removes expired vault directories
# ---------------------------------------------------------------------------


def test_secure_cleanup_removes_expired(tmp_path, monkeypatch):
    """A vault directory whose .expires marker is in the past should be removed."""
    # Create a fake SECURE_DIR with one expired vault
    secure_dir = tmp_path / "secure"
    vault_dir = secure_dir / "expiredvault001"
    vault_dir.mkdir(parents=True)

    # Write a file into the vault
    (vault_dir / "secret.csv").write_text("sensitive data")

    # Write an .expires marker 60 seconds in the past
    marker = vault_dir / ".expires"
    marker.write_text(str(int(time.time()) - 60))

    # Patch SECURE_DIR in the module
    mod = _load_cleanup_module()
    monkeypatch.setattr(mod, "SECURE_DIR", str(secure_dir))

    mod.main()

    assert not vault_dir.exists(), "Expired vault directory should have been removed"


def test_secure_cleanup_removes_multiple_expired(tmp_path, monkeypatch):
    """All expired vaults should be removed in a single cleanup pass."""
    secure_dir = tmp_path / "secure"
    vault_a = secure_dir / "vault_a"
    vault_b = secure_dir / "vault_b"
    for d in (vault_a, vault_b):
        d.mkdir(parents=True)
        (d / ".expires").write_text(str(int(time.time()) - 120))

    mod = _load_cleanup_module()
    monkeypatch.setattr(mod, "SECURE_DIR", str(secure_dir))
    mod.main()

    assert not vault_a.exists()
    assert not vault_b.exists()


# ---------------------------------------------------------------------------
# 5. secure-cleanup — keeps active (non-expired) vault directories
# ---------------------------------------------------------------------------


def test_secure_cleanup_keeps_active(tmp_path, monkeypatch):
    """A vault directory whose .expires marker is in the future should be kept."""
    secure_dir = tmp_path / "secure"
    vault_dir = secure_dir / "activevault001"
    vault_dir.mkdir(parents=True)

    (vault_dir / "report.pdf").write_bytes(b"%PDF-1.4 content")

    # Write an .expires marker 1 hour in the future
    marker = vault_dir / ".expires"
    marker.write_text(str(int(time.time()) + 3600))

    mod = _load_cleanup_module()
    monkeypatch.setattr(mod, "SECURE_DIR", str(secure_dir))
    mod.main()

    assert vault_dir.exists(), "Active vault directory should not have been removed"
    assert (vault_dir / "report.pdf").exists()


def test_secure_cleanup_keeps_active_no_marker(tmp_path, monkeypatch):
    """A vault directory with no .expires marker should be left untouched."""
    secure_dir = tmp_path / "secure"
    vault_dir = secure_dir / "nomarkervault"
    vault_dir.mkdir(parents=True)
    (vault_dir / "data.csv").write_text("row1")

    mod = _load_cleanup_module()
    monkeypatch.setattr(mod, "SECURE_DIR", str(secure_dir))
    mod.main()

    assert vault_dir.exists(), "Vault without .expires marker should not be removed"


def test_secure_cleanup_mixed_vaults(tmp_path, monkeypatch):
    """Cleanup removes only expired vaults and preserves active ones."""
    secure_dir = tmp_path / "secure"

    expired_dir = secure_dir / "expired_vault"
    active_dir = secure_dir / "active_vault"
    no_marker_dir = secure_dir / "no_marker_vault"

    for d in (expired_dir, active_dir, no_marker_dir):
        d.mkdir(parents=True)

    (expired_dir / ".expires").write_text(str(int(time.time()) - 1))
    (active_dir / ".expires").write_text(str(int(time.time()) + 7200))
    # no_marker_dir intentionally has no .expires file

    mod = _load_cleanup_module()
    monkeypatch.setattr(mod, "SECURE_DIR", str(secure_dir))
    mod.main()

    assert not expired_dir.exists(), "Expired vault should be removed"
    assert active_dir.exists(), "Active vault should be kept"
    assert no_marker_dir.exists(), "Vault without marker should be kept"


def test_secure_cleanup_no_secure_dir(tmp_path, monkeypatch):
    """cleanup should silently return when SECURE_DIR does not exist."""
    non_existent = str(tmp_path / "does_not_exist")
    mod = _load_cleanup_module()
    monkeypatch.setattr(mod, "SECURE_DIR", non_existent)
    # Should not raise
    mod.main()


def test_secure_cleanup_invalid_marker(tmp_path, monkeypatch):
    """A .expires marker with non-integer content is treated as 0 (expired)."""
    secure_dir = tmp_path / "secure"
    vault_dir = secure_dir / "bad_marker_vault"
    vault_dir.mkdir(parents=True)
    (vault_dir / ".expires").write_text("not-a-number")

    mod = _load_cleanup_module()
    monkeypatch.setattr(mod, "SECURE_DIR", str(secure_dir))
    mod.main()

    # expires == 0 means now >= 0 is always True → should be removed
    assert not vault_dir.exists(), "Vault with invalid marker (treated as epoch 0) should be removed"
