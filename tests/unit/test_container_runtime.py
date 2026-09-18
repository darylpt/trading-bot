"""Container baseline checks for the application runtime."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

try:
    from config.health import ContainerHealth, check_container_health
except ModuleNotFoundError:
    from src.config.health import ContainerHealth, check_container_health


def test_python_version_is_supported() -> None:
    assert sys.version_info >= (3, 11)


def test_core_environment_variables_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@postgres:5432/test")
    monkeypatch.setenv("BROKER_ENV", "paper")

    assert os.environ["DATABASE_URL"] == "postgresql://test:test@postgres:5432/test"
    assert os.environ["BROKER_ENV"] == "paper"


@pytest.mark.parametrize("module_name", ["pydantic", "pandas"])
def test_core_dependency_imports(module_name: str) -> None:
    assert importlib.util.find_spec(module_name) is not None
    assert importlib.import_module(module_name) is not None


def test_talib_imports_when_installed() -> None:
    if importlib.util.find_spec("talib") is None:
        pytest.skip("TA-Lib is optional outside the container image")

    assert importlib.import_module("talib") is not None


def test_build_context_excludes_credential_artifacts() -> None:
    dockerignore = Path(__file__).resolve().parents[2] / ".dockerignore"
    patterns = {
        line.strip()
        for line in dockerignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {"*.pem", "*.key", "*.crt"} <= patterns


def test_container_health_is_ok() -> None:
    health = check_container_health()

    assert isinstance(health, ContainerHealth)
    assert health.status == "OK"
    assert health.python_version == ".".join(str(part) for part in sys.version_info[:3])
    assert health.timestamp.tzinfo == timezone.utc
    assert health.timestamp <= datetime.now(timezone.utc)
