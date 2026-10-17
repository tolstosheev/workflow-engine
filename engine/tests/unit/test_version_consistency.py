"""Версия в пакетах, в FastAPI и в таблице релизов — одна и та же.

Число версий живёт в четырёх местах: `pyproject.toml`, `package.json`, FastAPI и
таблица тегов в `docs/GIT_WORKFLOW.md`. Раньше оно было продублировано константой
в `main.py`, и релиз 1.2.1 разошёлся с ней: в заголовке документации оставалось
1.2.0. Позже вышло хуже: релиз 1.3.0 был помечен тегом, а в пакетах осталось
1.2.1 — номер версии в OpenAPI показывал прошлый релиз, и никто этого не заметил.

Тесты закрывают обе возможности разойтись снова.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RELEASE_TABLE = ROOT / "docs" / "GIT_WORKFLOW.md"


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


def _recorded_releases() -> list[str]:
    return re.findall(
        r"^\| `(\d+\.\d+\.\d+)`", RELEASE_TABLE.read_text(encoding="utf-8"), re.MULTILINE
    )


def test_version_is_recorded_in_the_release_table() -> None:
    # Последняя строка таблицы — тот релиз, ради которого поднята версия в пакетах.
    # Подняли версию, но не записали строку: номер в OpenAPI обещает релиз, которого
    # нет. Записали строку, но не подняли версию: то же самое наоборот.
    recorded = _recorded_releases()

    assert recorded, "в docs/GIT_WORKFLOW.md нет таблицы тегов"
    assert _engine_version() == recorded[-1], (
        f"в пакетах {_engine_version()}, а последняя запись в таблице — {recorded[-1]}"
    )
