"""Иерархия ошибок приложения.

Каждая ошибка несёт HTTP-статус, чтобы обработчик в `main.py` не зависел от
типов исключений в нескольких местах.
"""

from __future__ import annotations


class AppError(Exception):
    """Базовая ошибка приложения."""

    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


class ValidationError(AppError):
    status_code = 422


class WorkflowNotRunnableError(ConflictError):
    """Инстанс в статусе, из которого нельзя запустить исполнение."""


class CompensationFailedError(ConflictError):
    """Компенсация исчерпала попытки — требуется ручное разбирательство."""


class LeaseLostError(AppError):
    """Инстанс перехвачен другим воркером: текущий обязан прекратить работу."""

    status_code = 409


class HandlerNotFoundError(AppError):
    status_code = 500


class DefinitionInvalidError(ValidationError):
    """Дефинишен не прошёл валидацию структуры или семантики графа."""
