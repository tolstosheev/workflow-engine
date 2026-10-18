"""Реестр воркеров (ADR-0018).

Проверяется не «воркер записался», а три границы, из которых складывается
смысл реестра: упавший виден как молчащий, простаивающий виден вообще, а
перезапуск с тем же идентификатором не плодит строки.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import registry as service
from tests.integration.test_instances_api import client as _client
from tests.integration.test_instances_api import db_session as _db_session

pytestmark = pytest.mark.integration

# Фикстуры переиспользуются как есть: оборачивать их в свои нельзя — pytest
# считает вызов фикстуры из фикстуры ошибкой, а не наследованием.
client = _client
db_session = _db_session


@pytest.fixture
async def session(db_session: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield db_session


async def _register(session: AsyncSession, worker_id: str) -> None:
    await service.register(session, worker_id)
    await session.commit()


async def _age_heartbeat(session: AsyncSession, worker_id: str, seconds: float) -> None:
    await session.execute(
        text("UPDATE workers SET heartbeat_at = heartbeat_at - make_interval(secs => :s)"),
        {"s": seconds},
    )
    await session.commit()


# --- Регистрация ------------------------------------------------------------


async def test_register_creates_row(session: AsyncSession) -> None:
    await _register(session, "worker-reg-1")

    rows = await service.list_workers(session, timedelta(seconds=3))
    assert [row.worker_id for row in rows] == ["worker-reg-1"]


async def test_register_twice_keeps_one_row(session: AsyncSession) -> None:
    # Перезапуск с тем же идентификатором обязан обновить строку, а не упасть на
    # уникальном индексе: система должна подниматься, а не отказывать себе в
    # подъёме после перезапуска.
    await _register(session, "worker-reg-2")
    await _register(session, "worker-reg-2")

    rows = await service.list_workers(session, timedelta(seconds=3))
    assert [row.worker_id for row in rows] == ["worker-reg-2"]


async def test_register_resets_counters_of_restarted_worker(session: AsyncSession) -> None:
    await _register(session, "worker-reg-3")
    await service.heartbeat(session, "worker-reg-3", cycles=10, steps=4)
    await session.commit()

    await _register(session, "worker-reg-3")

    row = (await service.list_workers(session, timedelta(seconds=3)))[0]
    # Счётчик нового воркера не должен начинаться с числа шагов предыдущего:
    # это выглядело бы так, будто он отработал чужую работу.
    assert row.cycles_completed == 0
    assert row.steps_executed == 0


# --- Сердце -----------------------------------------------------------------


async def test_heartbeat_counts_accumulate(session: AsyncSession) -> None:
    await _register(session, "worker-reg-4")
    await service.heartbeat(session, "worker-reg-4", cycles=1, steps=2)
    await service.heartbeat(session, "worker-reg-4", cycles=1, steps=3, lease_lost=1)
    await session.commit()

    row = (await service.list_workers(session, timedelta(seconds=3)))[0]
    assert (row.cycles_completed, row.steps_executed, row.lease_lost) == (2, 5, 1)


async def test_heartbeat_of_unknown_worker_adds_nothing(session: AsyncSession) -> None:
    # Сердце без регистрации — это не «создать воркера», а «никого не найти».
    # Создание здесь означало бы, что очистка таблицы не нужна: она сама
    # воскресила бы призраков.
    await _register(session, "worker-reg-5")
    await service.heartbeat(session, "worker-reg-ghost", cycles=3)
    await session.commit()

    rows = await service.list_workers(session, timedelta(seconds=3))
    assert [row.worker_id for row in rows] == ["worker-reg-5"]


# --- Состояние --------------------------------------------------------------


async def test_state_of_fresh_worker_is_active(session: AsyncSession) -> None:
    await _register(session, "worker-reg-6")
    now = datetime.now(UTC)

    row = (await service.list_workers(session, timedelta(seconds=3)))[0]
    assert service.worker_state(row, now, timedelta(seconds=3)) == service.ACTIVE


async def test_state_of_silent_worker_is_stale_not_absent(session: AsyncSession) -> None:
    # Главное свойство реестра. Упавший воркер обязан остаться видимым как
    # молчащий: если бы он исчезал, «воркер молчит» и «воркера нет» стали бы
    # одним и тем же, а вопрос о живости системы остался бы без ответа.
    await _register(session, "worker-reg-7")
    await _age_heartbeat(session, "worker-reg-7", 3600)

    now = datetime.now(UTC)
    row = (await service.list_workers(session, timedelta(seconds=3)))[0]
    assert service.worker_state(row, now, timedelta(seconds=3)) == service.STALE


async def test_state_of_stopped_worker_is_stopped(session: AsyncSession) -> None:
    # «Остановлен» и «не отвечает» — разные вещи, и различие между ними стирать
    # нельзя: по нему видно, упал воркер или его остановили.
    await _register(session, "worker-reg-8")
    await service.mark_stopped(session, "worker-reg-8")
    await session.commit()

    now = datetime.now(UTC)
    row = (await service.list_workers(session, timedelta(seconds=3)))[0]
    assert service.worker_state(row, now, timedelta(seconds=3)) == service.STOPPED
    assert row.stopped_at is not None


async def test_freshness_threshold_is_three_poll_intervals() -> None:
    assert service.stale_after(0.5) == timedelta(seconds=1.5)


# --- HTTP -------------------------------------------------------------------


async def test_workers_endpoint_lists_registered(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    await _register(session, "worker-http-1")

    response = await client.get("/api/workers")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["worker_id"] for item in body["items"]] == ["worker-http-1"]
    assert body["items"][0]["state"] == "active"
    # Граница отдаётся вместе со списком: подпись «не отвечает» без неё была бы
    # загадкой.
    assert body["fresh_for_seconds"] > 0


async def test_workers_endpoint_keeps_silent_worker_visible(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    await _register(session, "worker-http-2")
    await _age_heartbeat(session, "worker-http-2", 3600)

    items = (await client.get("/api/workers")).json()["items"]

    assert [item["worker_id"] for item in items] == ["worker-http-2"]
    assert items[0]["state"] == "stale"


async def test_busy_worker_is_not_reported_silent(session: AsyncSession) -> None:
    # Граница молчания — тройной интервал опроса, а сердце писалось раз за цикл, а
    # цикл умел выполнить до 100 шагов. Замер на стенде: под нагрузкой оба занятых
    # исполнителя мигнули «не отвечает» по семь раз за сорок опросов, то есть
    # система называла мёртвым того, кто работает. Ровно то, что реестр и отменил
    # своим появлением: «молчит» должно значить «живого нет».
    import asyncio

    from app.core.config import get_settings
    from app.core.db import get_session_factory
    from app.services import registry as registry_service

    settings = get_settings()
    boundary = registry_service.stale_after(settings.worker_poll_interval_seconds).total_seconds()

    await _register(session, "worker-busy")

    # Имитация занятого цикла: столько времени проходит без записи сердца, сколько
    # цикл держится под нагрузкой, — а границу молчания это перекрывает.
    await _age_heartbeat(session, "worker-busy", boundary + 1.5)
    await asyncio.sleep(boundary / 4)

    async with get_session_factory()() as own_session:
        await registry_service.touch(own_session, "worker-busy")
        await own_session.commit()

    rows = await service.list_workers(session, timedelta(seconds=3))
    assert service.worker_state(rows[0], datetime.now(UTC), timedelta(seconds=3)) == "active"


async def test_worker_loop_reports_work_and_confirms_it_is_alive(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тело цикла — то, что видно только в реестре.

    Все прочие проверки `run_worker` шли на пустой очереди, а пустая очередь не
    исполняет ни одной строки этого тела: счётчики оставались нулевыми, и
    подтверждение пульса внутри длинного прохода не вызывалось никогда. То
    есть правку против «воркер молчит на занятом» можно было сломать, и ничто
    не краснело — её проверяли замером под нагрузкой, а замер не краснеет.
    """
    import asyncio

    from app import worker

    settings = worker.get_settings().model_copy(update={"worker_poll_interval_seconds": 0.05})
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    # Граница молчания урезана до доли секунды: проверка не должна ждать
    # настоящего интервала опроса, иначе она станет проверкой на удачу.
    monkeypatch.setattr(service, "stale_after", lambda interval: timedelta(seconds=0.05))

    stop_event = asyncio.Event()
    beats: list[tuple[int, int, int]] = []
    touches = {"n": 0}
    calls = {"n": 0}
    real_heartbeat, real_touch = service.heartbeat, service.touch

    async def _heartbeat(db_session: AsyncSession, worker_id: str, **counters: int) -> None:
        beats.append((counters["cycles"], counters["steps"], counters["lease_lost"]))
        await real_heartbeat(db_session, worker_id, **counters)

    async def _touch(db_session: AsyncSession, worker_id: str) -> None:
        touches["n"] += 1
        await real_touch(db_session, worker_id)

    async def _cycle(worker_id: str, cache: dict) -> worker.CycleResult:
        calls["n"] += 1
        if calls["n"] > 60:
            stop_event.set()
            return worker.CycleResult(
                handled=False, step_executed=False, terminal=False, lease_lost=False
            )
        await asyncio.sleep(0.002)
        # Первый цикл теряет аренду: без паузы воркер забрал бы следующий
        # инстанс и упал бы на нём тем же самым.
        return worker.CycleResult(
            handled=True,
            step_executed=True,
            terminal=False,
            lease_lost=calls["n"] == 1,
        )

    monkeypatch.setattr(service, "heartbeat", _heartbeat)
    monkeypatch.setattr(service, "touch", _touch)
    monkeypatch.setattr(worker, "run_batch_cycle", _cycle)

    await asyncio.wait_for(worker.run_worker(stop_event), timeout=30)

    # Счётчики доходят до сердца: иначе панель показывает работающий исполнитель,
    # который ничего не сделал.
    assert beats, "сердце воркера не записано ни разу"
    assert max(c for c, _, _ in beats) > 0
    assert max(s for _, s, _ in beats) > 0
    assert max(lost for _, _, lost in beats) >= 1, "потеря аренды не попала в сердце"
    # Подтверждение внутри длинного прохода: цикл умеет выполнить сто шагов, и
    # одна запись сердца на цикл означала бы «молчит» на занятом стенде.
    assert touches["n"] >= 1
    assert "cycle.aborted" in [record.getMessage() for record in caplog.records]


async def test_worker_loop_registers_itself(session: AsyncSession) -> None:
    # Цикл воркера обязан вписаться в реестр сам: ручная регистрация означала бы,
    # что в реальном запуске строки не будет, а тест проверял бы то, чего нет.
    import asyncio

    from app import worker

    stop_event = asyncio.Event()
    task = asyncio.create_task(worker.run_worker(stop_event))
    await asyncio.sleep(0.2)
    stop_event.set()
    await asyncio.wait_for(task, timeout=10)

    rows = await service.list_workers(session, timedelta(seconds=3))
    assert len(rows) == 1
    assert rows[0].stopped_at is not None


# --- Давно завершённые -------------------------------------------------------


async def test_departed_rows_are_hidden_by_default(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    # Идентификатор воркера включает случайную часть, а имя узла у контейнера
    # меняется при каждом пересоздании: за неделю стенда реестр набирает строки
    # всех, кто когда-либо запускался. Панель из них состояла «12 в системе»,
    # хотя отвечали трое.
    await _register(session, "worker-reg-old")
    await _age_heartbeat(session, "worker-reg-old", 3 * 24 * 3600)

    items = (await client.get("/api/workers")).json()["items"]

    assert [item["worker_id"] for item in items] == []


async def test_departed_rows_come_back_on_request(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    await _register(session, "worker-reg-old2")
    await _age_heartbeat(session, "worker-reg-old2", 3 * 24 * 3600)

    items = (await client.get("/api/workers?departed=true")).json()["items"]

    # Данные не удаляются (ADR-0018): за переключателем строка на месте.
    assert [item["worker_id"] for item in items] == ["worker-reg-old2"]


async def test_hidden_rows_are_counted(session: AsyncSession, client: httpx.AsyncClient) -> None:
    # Без числа переключатель выглядел бы пустым, и человек решил бы, что
    # никто никогда не выходил из строя.
    await _register(session, "worker-reg-old3")
    await _age_heartbeat(session, "worker-reg-old3", 3 * 24 * 3600)

    body = (await client.get("/api/workers")).json()

    assert body["departed_total"] == 1


async def test_recent_stopped_worker_is_not_hidden(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    # Только что остановленный воркер — часть картины: он объясняет, почему
    # процессы перестали продвигаться.
    await _register(session, "worker-reg-recent")
    await service.mark_stopped(session, "worker-reg-recent")
    await session.commit()

    items = (await client.get("/api/workers")).json()["items"]

    assert [item["worker_id"] for item in items] == ["worker-reg-recent"]


async def test_departed_threshold_is_a_day() -> None:
    assert timedelta(hours=24) == service.DEPARTED_AFTER
