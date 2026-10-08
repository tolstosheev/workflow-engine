"""Тесты структурированного логирования."""

from __future__ import annotations

import json
import logging

from app.core.config import get_settings
from app.core.logging import JsonFormatter, configure_logging


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.WARNING,
        pathname=__file__,
        lineno=10,
        msg="шаг не выполнен",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_level_logger_and_message() -> None:
    payload = json.loads(JsonFormatter().format(_record()))

    assert payload["level"] == "WARNING"
    assert payload["logger"] == "test.logger"
    assert payload["message"] == "шаг не выполнен"


def test_json_formatter_includes_structured_fields() -> None:
    # Структурные поля обязаны попадать в вывод: без них лог нечитаем в агрегаторе.
    payload = json.loads(JsonFormatter().format(_record(instance_id="abc", attempt=2)))

    assert payload["instance_id"] == "abc"
    assert payload["attempt"] == 2


def test_json_formatter_drops_internal_record_attributes() -> None:
    payload = json.loads(JsonFormatter().format(_record()))

    assert "pathname" not in payload
    assert "lineno" not in payload


def test_json_formatter_serialises_exception() -> None:
    try:
        raise ValueError("сбой сервиса")
    except ValueError:
        import sys

        record = _record()
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError: сбой сервиса" in payload["exception"]


def test_configure_logging_installs_single_handler(monkeypatch: object) -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging()
        assert len(root.handlers) == 1
        assert root.level == logging.INFO
    finally:
        root.handlers = before


def test_configure_logging_uses_text_formatter_by_default() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging()
        formatter = root.handlers[0].formatter
        assert not isinstance(formatter, JsonFormatter)
    finally:
        root.handlers = before


def test_configure_logging_uses_json_formatter_when_enabled(
    monkeypatch: object,
) -> None:
    monkeypatch.setattr("app.core.logging.get_settings", _settings_with_json_logging)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging()
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers = before


def test_configure_logging_clears_uvicorn_handlers() -> None:
    # Иначе каждая запись дублируется: свой handler у uvicorn плюс propagate в root.
    access = logging.getLogger("uvicorn.access")
    access.addHandler(logging.NullHandler())
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging()
        assert access.handlers == []
        assert access.propagate is True
    finally:
        root.handlers = before


def _settings_with_json_logging() -> object:
    settings = get_settings()
    object.__setattr__(settings, "log_json", True)
    return settings
