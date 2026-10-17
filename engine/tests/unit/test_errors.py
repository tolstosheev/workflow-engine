"""Тесты иерархии ошибок.

Статусы проверяются явно: роутер строит ответ по `status_code`, и неверное
значение превращается в 500 вместо ожидаемого 404 или 409.
"""

from __future__ import annotations

import pytest

from app.core.errors import (
    AppError,
    ConflictError,
    DefinitionInvalidError,
    HandlerNotFoundError,
    LeaseLostError,
    NotFoundError,
    ValidationError,
    WorkflowNotRunnableError,
)


@pytest.mark.parametrize(
    ("error_class", "expected_status"),
    [
        (AppError, 500),
        (NotFoundError, 404),
        (ConflictError, 409),
        (ValidationError, 422),
        (HandlerNotFoundError, 500),
        (LeaseLostError, 409),
        (DefinitionInvalidError, 422),
        (WorkflowNotRunnableError, 409),
    ],
)
def test_status_codes(error_class: type[AppError], expected_status: int) -> None:
    assert error_class("x").status_code == expected_status


@pytest.mark.parametrize(
    "error_class",
    [WorkflowNotRunnableError, DefinitionInvalidError],
)
def test_specialised_errors_inherit_their_base(error_class: type[AppError]) -> None:
    # Наследование важно для обработчика: он ловит один базовый класс,
    # поэтому подкласс обязан сохранять его статус.
    assert issubclass(error_class, AppError)


def test_workflow_not_runnable_is_conflict() -> None:
    assert issubclass(WorkflowNotRunnableError, ConflictError)


def test_definition_invalid_is_validation_error() -> None:
    assert issubclass(DefinitionInvalidError, ValidationError)


def test_message_is_available_on_instance() -> None:
    error = NotFoundError("процесс не найден")

    assert error.message == "процесс не найден"
    assert str(error) == "процесс не найден"
