"""Сверяет реестр записей с самими записями.

Расхождение возникало дважды: ADR-0021 и ADR-0023 появились на диске и в
работе, но в `docs/adr/README.md` их не было, а у ADR-0019 и ADR-0020 в
реестре остался `Proposed`, когда код по ним уже написан. Обе ошибки не
мешали ни тестам, ни стенду — их замечали только при вычитке.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ADR_DIR = ROOT / "docs" / "adr"
REGISTRY = ADR_DIR / "README.md"

# Записи писались в трёх форматах: ранние — таблицей с `Status`, поздние —
# строкой `Статус`, одна — `Status:`. Формат различается, утверждение одно.
STATUS = re.compile(
    r"\*\*Статус\*\*:\s*(\S+)|\*\*Status:\*\*\s*(\S+)|\|\s*\*\*Status\*\*\s*\|\s*(\S+)"
)
REGISTRY_ROW = re.compile(r"^\|\s*\[(\d{4})\]\(([^)]+)\)\s*\|([^|]*)\|\s*([^|]*)\|$", re.M)


def _adrs() -> list[tuple[str, Path]]:
    return sorted((path.name[:4], path) for path in ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))


def _rows() -> dict[str, tuple[str, str]]:
    text = REGISTRY.read_text(encoding="utf-8")
    return {
        number: (link, status.strip())
        for number, link, _title, status in REGISTRY_ROW.findall(text)
    }


def test_every_adr_is_in_the_registry() -> None:
    # Реестр — единственное место, где перечислены принятые решения. Запись,
    # которой в нём нет, читается как «её не принимали».
    rows = _rows()
    missing = [number for number, _path in _adrs() if number not in rows]

    assert missing == [], f"записи вне реестра: {missing}"


def test_registry_links_resolve() -> None:
    # Ссылка, ведущая в никуда, хуже отсутствующей строки: читатель видит
    # решение и не находит его.
    broken = [link for link, _status in _rows().values() if not (ADR_DIR / link).exists()]

    assert broken == [], f"ссылки реестра не открываются: {broken}"


@pytest.mark.parametrize("number", sorted({number for number, _ in _adrs()}))
def test_registry_status_matches_the_record(number: str) -> None:
    # Статус в реестре и в записи — два места, где живёт одно и то же
    # утверждение; расхождение читается как «принято» там, где код не написан.
    rows = _rows()
    if number not in rows:
        pytest.skip("отсутствие в реестре проверяет другой тест")

    path = ADR_DIR / next(path.name for num, path in _adrs() if num == number)
    found = STATUS.search(path.read_text(encoding="utf-8"))
    assert found is not None, f"{path.name}: нет строки статуса"

    declared = found.group(1) or found.group(2) or found.group(3)
    listed = rows[number][1]
    # «Accepted (по умолчанию выключен)» — тот же статус с уточнением, поэтому
    # сравнивается первое слово.
    assert declared == listed.split()[0], f"{path.name}: {declared} против {listed}"
