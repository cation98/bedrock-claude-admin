"""Tests for DRM mode in OnlyOffice config (Task 2 — Bundle 6)."""

import os

import pytest

os.environ.setdefault("ONLYOFFICE_JWT_SECRET", "test-onlyoffice-jwt-secret-32-chars-min-xx")


from app.core.config import get_settings
from app.routers.viewers import _build_onlyoffice_config


@pytest.fixture
def settings():
    return get_settings()


def test_drm_mode_all_permissions_false(settings):
    """drm_mode=True → all permissions must be False."""
    config = _build_onlyoffice_config(
        "report.pdf",
        "alice",
        "Alice",
        settings,
        drm_mode=True,
    )
    perms = config["document"]["permissions"]
    for key, val in perms.items():
        assert val is False, f"permissions.{key} should be False in DRM mode, got {val}"
    assert perms.get("copy") is False, "permissions.copy must be explicitly False in DRM mode"


def test_drm_mode_customization(settings):
    """drm_mode=True → hideRightMenu=True and help=False in customization."""
    config = _build_onlyoffice_config(
        "slides.pptx",
        "alice",
        "Alice",
        settings,
        drm_mode=True,
    )
    custom = config["editorConfig"]["customization"]
    assert custom["hideRightMenu"] is True
    assert custom["help"] is False
    assert custom["chat"] is False
    assert custom["comments"] is False


def test_normal_mode_editable_permissions(settings):
    """drm_mode=False (default) + editable=True → download/edit True."""
    config = _build_onlyoffice_config(
        "doc.docx",
        "bob",
        "Bob",
        settings,
        editable=True,
        drm_mode=False,
    )
    perms = config["document"]["permissions"]
    assert perms["download"] is True
    assert perms["edit"] is True


def test_normal_mode_view_only_permissions(settings):
    """drm_mode=False, editable=False → all permissions False (view-only)."""
    config = _build_onlyoffice_config(
        "report.pdf",
        "bob",
        "Bob",
        settings,
        editable=False,
        drm_mode=False,
    )
    perms = config["document"]["permissions"]
    assert perms["download"] is False
    assert perms["edit"] is False


def test_drm_mode_view_mode(settings):
    """drm_mode=True → editorConfig.mode must be 'view'."""
    config = _build_onlyoffice_config(
        "report.pdf",
        "alice",
        "Alice",
        settings,
        drm_mode=True,
    )
    assert config["editorConfig"]["mode"] == "view"
