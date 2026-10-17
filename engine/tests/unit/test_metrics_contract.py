"""Разрез по статусам не имеет права разойтись с движком.

Список на странице метрик написан вручную, и добавленное движком состояние из
него выпадало: страница показывала по нему ноль, то есть утверждала, что таких
процессов нет.

Тест читает `metrics.tsx` — он лежит за пределами пакета интерфейса, поэтому
сверка живёт здесь, рядом с перечислением — источником истины.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.models.enums import ACTIVE_STATUSES, TERMINAL_STATUSES

METRICS_LIB = Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "metrics.ts"


def _shown_statuses() -> list[str]:
    text = METRICS_LIB.read_text(encoding="utf-8")
    block = re.search(r"BREAKDOWN_STATUSES: InstanceStatus\[\] = \[(.*?)\]", text, re.S)
    assert block, "в lib/metrics.ts нет списка BREAKDOWN_STATUSES"
    return re.findall(r"'([A-Z_]+)'", block.group(1))


def test_every_open_status_reaches_the_breakdown() -> None:
    missing = sorted(
        status.value for status in ACTIVE_STATUSES if status.value not in _shown_statuses()
    )

    assert not missing, f"статусы не попали в разрез метрик: {missing}"


def test_the_breakdown_shows_only_real_statuses() -> None:
    # Лишний элемент не ломает страницу, но означает опечатку: он тихо
    # занимает место настоящего значения и всегда показывает ноль.
    known = {status.value for status in ACTIVE_STATUSES | TERMINAL_STATUSES}

    unknown = [status for status in _shown_statuses() if status not in known]

    assert not unknown, f"в разрезе статусы, которых нет у инстанса: {unknown}"


def test_the_breakdown_lists_no_status_twice() -> None:
    shown = _shown_statuses()

    assert len(set(shown)) == len(shown), f"статус повторяется в разрезе: {shown}"
