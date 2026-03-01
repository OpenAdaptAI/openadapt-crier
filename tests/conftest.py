"""Pytest configuration for crier tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Remove CRIER_ env vars to prevent test pollution from the real environment."""
    import os

    for key in list(os.environ):
        if key.startswith("CRIER_"):
            monkeypatch.delenv(key, raising=False)
