"""Тест-страж: обработчики из редактора схем должны существовать.

Схема хранится строкой, и опечатка в имени обработчика была видна только при
сохранении, а образец в редакторе — первое, с чем сталкивается человек.

Тест читает `definitions.tsx` и требует, чтобы каждый указанный обработчик
импортировался. Разбор `.tsx` регулярным выражением сломается при переносе
редактора, но упадёт явно — это лучше молчаливого расхождения.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.handlers.registry import is_handler_importable

pytestmark = pytest.mark.integration

SAMPLE_FILE = Path(__file__).resolve().parents[3] / "frontend" / "src" / "pages" / "definitions.tsx"

# Обработчики в образце: `app.demo.handlers:reserve_inventory`
HANDLER_REF = re.compile(r'"([a-zA-Z_][\w.]*:[a-zA-Z_]\w*)"')


def test_editor_sample_exists() -> None:
    assert SAMPLE_FILE.exists(), f"образец определения не найден: {SAMPLE_FILE}"


def test_sample_handlers_are_importable() -> None:
    text = SAMPLE_FILE.read_text(encoding="utf-8")
    refs = sorted(set(HANDLER_REF.findall(text)))

    assert refs, "в образце не найдено ни одной ссылки на обработчик"

    missing = [ref for ref in refs if not is_handler_importable(ref)]
    assert not missing, f"образец ссылается на несуществующие обработчики: {missing}"


def test_sample_saves_without_conflict() -> None:
    """Образец не должен совпадать по имени и версии с тем, что создаёт seed.

    `scripts/seed.py` заводит `order_fulfillment` версии 1. Образец с тем же
    именем нельзя сохранить повторно: сервер вернёт конфликт, и первый же
    показ на подготовленной базе провалится.
    """
    from scripts.definitions import DEFINITION_NAME, DEMO_VERSION

    text = SAMPLE_FILE.read_text(encoding="utf-8")
    name_match = re.search(r'"name":\s*"([^"]+)"', text)

    assert name_match is not None, "в образце нет поля name"
    assert (name_match.group(1), DEMO_VERSION) != (
        DEFINITION_NAME,
        DEMO_VERSION,
    ), (
        "образец совпадает с определением из scripts/definitions.py — второй раз сохранить его нельзя"
    )


def test_sample_uses_seeded_sku() -> None:
    """Артикул во входных данных берётся из склада, который наполняет seed."""
    from scripts.definitions import DEMO_INVENTORY

    text = SAMPLE_FILE.read_text(encoding="utf-8")
    skus = set(re.findall(r'"sku":\s*"(SKU-\w+)"', text))
    known = {row["sku"] for row in DEMO_INVENTORY}

    assert skus, "в образце не заданы входные данные"
    assert skus <= known, f"образец ссылается на артикулы вне склада: {sorted(skus - known)}"
