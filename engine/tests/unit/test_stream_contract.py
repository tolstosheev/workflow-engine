"""Контракт потока событий проверяется, а не подразумевается.

Бэкенд решает, какие события уходят в поток (`NOTIFIABLE`), а интерфейс
подписывается на свой список. Списки живут в разных файлах и разных языках,
и расходятся молча: событие, которого нет в потоке, экран не увидит, а его
обработчик в редьюсере останется недостижимым. Так и случилось — обработчики
`TIMER_SCHEDULED`, `TIMER_FIRED` и `STEP_STARTED` были, а поток их не слал.

Тест читает оба объявления и требует совпадения. Стоит строк, а ловит
дефект, который иначе обнаружился бы только глазами на стенде.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models.enums import EventType
from app.routers.stream import NOTIFIABLE, SILENT

ROOT = Path(__file__).resolve().parents[3]
CLIENT = ROOT / "frontend" / "src" / "api" / "stream.ts"


def _client_subscriptions() -> set[str]:
    text = CLIENT.read_text(encoding="utf-8")
    match = re.search(r"SUBSCRIBED_EVENT_TYPES[^=]*=\s*\[(.*?)\]", text, re.S)
    assert match, "в stream.ts нет списка SUBSCRIBED_EVENT_TYPES"
    return set(re.findall(r"'([A-Z_]+)'", match.group(1)))


@pytest.mark.skipif(not CLIENT.exists(), reason="исходники интерфейса не в этом дереве")
def test_client_subscribes_to_exactly_the_notifiable_events() -> None:
    client = _client_subscriptions()
    backend = {event.value for event in NOTIFIABLE}

    missing = backend - client
    assert not missing, f"поток шлёт события, на которые клиент не подписан: {sorted(missing)}"

    extra = client - backend
    assert not extra, f"клиент ждёт события, которых нет в потоке: {sorted(extra)}"


def test_every_event_type_is_decided_one_way_or_another() -> None:
    # Каждый тип события либо уходит в поток, либо помечен как намеренно
    # молчащий в `SILENT`. Тип, выпавший из обоих списков, — это событие, о
    # котором забыли: оно пишется в журнал и не доходит до интерфейса.
    undecided = {
        event.value for event in EventType if event not in NOTIFIABLE and event not in SILENT
    }
    assert not undecided, f"события без решения по потоку: {sorted(undecided)}"
