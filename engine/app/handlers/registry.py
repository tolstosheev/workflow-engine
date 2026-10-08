"""Резолв обработчиков шагов по строке `module:function`.

Дефинишен ссылается на код строкой, а не импортом: иначе добавление нового
процесса требовало бы изменения кода движка. Разрешение откладывается до
момента исполнения шага, а проверка импортируемости входит в семантическую
валидацию — ошибка должна находиться при сохранении дефинишена, а не через
час после запуска инстанса.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from app.core.errors import HandlerNotFoundError

Handler = Callable[..., Any]


@lru_cache(maxsize=512)
def resolve_handler(reference: str) -> Handler:
    """Импортирует модуль и достаёт функцию по строке `module:function`.

    Результат кэшируется: импорт на каждом шаге дорог, а набор дефинишенов
    ограничен.
    """
    module_path, _, function_name = reference.partition(":")
    if not module_path or not function_name:
        raise HandlerNotFoundError(
            f"обработчик {reference!r} задан неверно: ожидается 'module:function'"
        )

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise HandlerNotFoundError(
            f"обработчик {reference!r}: модуль {module_path!r} не импортируется ({exc})"
        ) from exc

    handler: Any = getattr(module, function_name, None)
    if handler is None:
        raise HandlerNotFoundError(
            f"обработчик {reference!r}: в модуле {module_path!r} нет функции {function_name!r}"
        )
    # Проверяется именно функция, а не callable: класс вызываем (конструктор),
    # но обработчиком быть не может — вызов с контекстом упал бы в момент
    # исполнения шага, а не при валидации дефинишена.
    if isinstance(handler, type) or not inspect.isroutine(handler):
        raise HandlerNotFoundError(f"обработчик {reference!r} не является функцией")
    return handler


def is_handler_importable(reference: str) -> bool:
    """Проверяет разрешимость обработчика без выполнения.

    Нужна валидации дефинишена; вызывать обработчик нельзя — это запустило бы
    побочные эффекты.
    """
    try:
        resolve_handler(reference)
    except HandlerNotFoundError:
        return False
    return True


def clear_handler_cache() -> None:
    """Сбрасывает кэш. Используется при выгрузке модулей в тестах."""
    resolve_handler.cache_clear()
