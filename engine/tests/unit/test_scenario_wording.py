"""Сценарий про закрепление не должен обещать перехват другим исполнителем.

На стенде с одним исполнителем перехватывать некому: процесс поднимает он же
после истечения TTL, и формулировка про «другого» выглядела доказанной, потому
что статус доходил до конца.

Названия приведены к словарю `docs/UI-GLOSSARY.md`, и проверка обязана искать то,
что теперь написано.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = ROOT / "frontend" / "src" / "lib" / "scenarios.ts"
OPERATIONS = ROOT / "docs" / "OPERATIONS.md"


def _lease_scenario() -> str:
    text = SCENARIOS.read_text(encoding="utf-8")
    start = text.find("id: 'lease-expired'")
    assert start != -1, "сценарий про закрепление не найден"
    return text[start : start + 2000]


def test_scenario_does_not_claim_another_executor() -> None:
    scenario = _lease_scenario()

    # На одном исполнителе перехвата нет, и обещание «забирает другой»
    # проверяется на защите человеком, который знает конфигурацию стенда.
    assert "другой исполнитель" not in scenario.split("title:")[0]


def test_scenario_says_what_it_needs() -> None:
    scenario = _lease_scenario()

    # Условие должно быть названо рядом с утверждением, а не в документации,
    # которую читают после, когда замечать уже поздно.
    assert "двух исполнителей" in scenario


def test_scenario_states_what_it_proves() -> None:
    scenario = _lease_scenario()

    assert "переживает" in scenario or "не зависает" in scenario


def test_operations_explains_how_to_get_two_workers() -> None:
    text = OPERATIONS.read_text(encoding="utf-8")

    # Команда без `--no-deps` поднимает весь стенд, включая второй сервис
    # исполнителей: это уже ловилось с замерами.
    assert re.search(r"up -d --no-deps --scale worker=2", text)
    assert "/api/workers" in text


def test_operations_table_does_not_promise_a_handover() -> None:
    text = OPERATIONS.read_text(encoding="utf-8")

    assert "перехват процесса другим исполнителем" not in text
