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

from app.core.config import get_settings
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
    _ensure_module_allowed(module_path, reference)

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


def _ensure_module_allowed(module_path: str, reference: str) -> None:
    """Проверяет, что модуль лежит в разрешённом корне.

    Определение сохраняется через API, и без этой проверки строка `os:system`
    исполнилась бы в процессе воркера: дефинишен ссылается на код именем, а не
    импортом (ADR-0013).
    """
    allowed = _allowed_roots()
    root = module_path.split(".", 1)[0]
    if root in allowed:
        return
    raise HandlerNotFoundError(
        f"обработчик {reference!r}: модуль {module_path!r} вне разрешённых "
        f"({', '.join(sorted(allowed))})"
    )


def _allowed_roots() -> frozenset[str]:
    """Корни модулей, из которых разрешено брать обработчики.

    Настройка приходит из окружения строкой через запятую: список из JSON в
    `.env` выглядел бы неуместно, а разбирать его должен кто-то один.
    """
    raw = get_settings().handler_module_allowlist
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


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
