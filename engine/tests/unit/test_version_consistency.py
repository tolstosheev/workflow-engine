"""Версия в документации API и в пакете — одна и та же.

Число версий живёт в трёх местах: `pyproject.toml`, `package.json` и FastAPI.
Раньше оно было продублировано константой в `main.py`, и релиз 1.2.1 разошёлся
с ней: в заголовке документации оставалось 1.2.0. Тест закрывает саму
возможность разойтись снова.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _engine_version() -> str:
    data = tomllib.loads((ROOT / "engine" / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _frontend_version() -> str:
    data = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    return str(data["version"])


def test_api_reports_the_package_version() -> None:
    from app.main import api_version

    assert api_version() == _engine_version()


def test_frontend_package_matches_backend_package() -> None:
    # Фронтенд и бэкенд выкатываются вместе: расхождение версий означало бы,
    # что собранный интерфейс не соответствует выложенному API.
    assert _frontend_version() == _engine_version()
