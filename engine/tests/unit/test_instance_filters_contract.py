"""Договор вкладок списка: каждое состояние движка видно хотя бы под одной.

Кнопки фильтра делались как точные статусы, и вкладка «Завершённые» показывала
ровно `COMPLETED`, то есть на стенде с преобладанием отменённых процессов
выглядела пустой. Перечисление движка — источник истины, сверка зеркалит ту,
что уже есть для разреза метрик.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIST = ROOT / "frontend" / "src" / "lib" / "instanceList.ts"
ENUMS = ROOT / "engine" / "app" / "models" / "enums.py"

STATUS = re.compile(r"^\s{4}([A-Z][A-Z_]+)\s*=", re.M)
# Переводы строк между полями допускаются: стража не должна требовать от
# исходника определённого вида — иначе её правят вместе с оформлением, а не
# вместе с содержанием.
GROUP = re.compile(r"id:\s*'[a-z-]+',\s*label:\s*'[^']+',\s*statuses:\s*\[(.*?)\]", re.S)
ITEM = re.compile(r"'([A-Z][A-Z_]+)'")


def engine_statuses() -> set[str]:
    """Состояния из перечисления движка — источник истины, не из интерфейса.

    Тело класса обрывается на первой строке без отступа, а не на первой пустой:
    иначе разбор останавливался на строке документации и возвращал пустое
    множество, а сверка с ним проходила вхолостую.
    """
    text = ENUMS.read_text(encoding="utf-8")
    block = re.search(r"class InstanceStatus\([^)]*\):\n(.*?)(?=\n\n\S)", text, re.S)
    assert block is not None, "не найдено перечисление InstanceStatus"
    return set(STATUS.findall(block.group(1)))


def frontend_groups() -> dict[str, set[str]]:
    text = LIST.read_text(encoding="utf-8")
    groups = {}
    for match in GROUP.finditer(text):
        label = re.search(r"label:\s*'([^']+)'", match.group(0))
        statuses = set(ITEM.findall(match.group(1)))
        if label:
            groups[label] = statuses
    return groups


def test_every_engine_status_has_a_tab() -> None:
    engine = engine_statuses()
    # Охрана от пустой сверки: пустое множество «покрыто» всем, и проверка
    # проходила бы, ничего не проверяя. Так и случилось в первой версии.
    assert len(engine) >= 10, (
        f"из движка разобрано {len(engine)} состояний — сверка ничего не видит"
    )
    covered: set[str] = set()
    for statuses in frontend_groups().values():
        covered |= statuses
    missing = sorted(engine_statuses() - covered)
    assert not missing, (
        f"состояния движка без вкладки: {missing} — процесс в них виден только "
        "под «Все», а вкладка с названием врёт о том, что показывает"
    )


def test_tabs_do_not_invent_statuses() -> None:
    known = engine_statuses()
    invented = sorted(
        status for statuses in frontend_groups().values() for status in statuses - known
    )
    assert not invented, f"вкладки ссылаются на состояния, которых нет в движке: {invented}"


def test_every_status_belongs_to_exactly_one_tab() -> None:
    # Статус в двух вкладках означает, что нажатие одной кнопки незаметно
    # меняет содержимое другой — то есть фильтр работает не с тем, что написано.
    seen: dict[str, list[str]] = {}
    for label, statuses in frontend_groups().items():
        for status in statuses:
            seen.setdefault(status, []).append(label)
    doubled = {status: labels for status, labels in seen.items() if len(labels) > 1}
    assert not doubled, f"состояния попали в несколько вкладок: {doubled}"


def test_tabs_are_discovered() -> None:
    """Охрана от vacuous: разбор пуст — это «проверять нечего», а не «всё в порядке»."""
    groups = frontend_groups()
    assert len(groups) >= 5, f"разобрано вкладок: {len(groups)} — сверка ничего не проверяет"
