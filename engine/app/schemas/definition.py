"""Схема определения workflow (структурный уровень валидации).

Проверяет форму: типы, обязательные поля, форматы. Семантика графа —
разрешимость переходов, отсутствие бесконечных циклов, существование
обработчиков — проверяется отдельно в `definition_service`.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Имя шага используется как ключ словаря steps и попадает в SQL-запросы
# выборки, поэтому ограничение защищает не только от опечаток.
StepName = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$")]
HandlerRef = Annotated[
    str,
    Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_.]*:[a-zA-Z_][a-zA-Z0-9_]*$"),
]


class RetryPolicy(BaseModel):
    """Политика повторов шага."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=100)
    backoff_seconds: float = Field(default=1.0, ge=0, le=3600)
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    # Потолок задержки. Без него `backoff_seconds * multiplier**(n-1)` при
    # разрешённых границах даёт ожидание на миллиарды секунд: процесс ушёл бы
    # в таймер за пределы срока службы.
    max_backoff_seconds: float = Field(default=300.0, ge=0, le=86_400)


class Step(BaseModel):
    """Шаг графа.

    `next` — строка или объект с ветвлением по результату. Строковый вариант
    подразумевает `on_success`.
    """

    model_config = ConfigDict(extra="forbid")

    action: HandlerRef
    compensation: HandlerRef | None = None
    next: StepName | dict[str, StepName] | None = None
    retry: RetryPolicy | None = None
    wait_before_seconds: float = Field(default=0, ge=0, le=86_400)
    timeout_seconds: float = Field(default=30, ge=0.1, le=86_400)
    loop: bool = False
    max_iterations: int | None = Field(default=None, ge=1, le=10_000)
    description: str | None = None

    @field_validator("max_iterations")
    @classmethod
    def _max_iterations_requires_loop(cls, value: int | None, info: object) -> int | None:
        data = getattr(info, "data", {})
        if value is not None and not data.get("loop"):
            raise ValueError(
                "max_iterations имеет смысл только вместе с loop: true — иначе "
                "ограничение числа проходов ничем не поддерживается"
            )
        return value


class ForkStep(Step):
    """Ветвление: параллельные ветки расходятся после этого шага."""

    fork: dict[str, list[StepName]] = Field(min_length=1)


class JoinStep(Step):
    """Слияние параллельных веток.

    `join` перечисляет шаги, все из которых должны завершиться; после этого
    выполняется `next`.
    """

    join: list[StepName] = Field(min_length=1)


class WorkflowDefinitionSchema(BaseModel):
    """Корень определения.

    Имена полей короче обычных (`next` вместо `next_steps`) ради читаемости
    JSON: дефинишен читают глазами чаще, чем пишут.
    """

    model_config = ConfigDict(extra="forbid")

    start: StepName
    steps: dict[StepName, ForkStep | JoinStep | Step] = Field(min_length=1)
    description: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("steps")
    @classmethod
    def _key_matches_name(
        cls, value: dict[str, ForkStep | JoinStep | Step]
    ) -> dict[str, ForkStep | JoinStep | Step]:
        # Ключ словаря — идентичность шага для переходов. Расхождение ключа и
        # шага делало бы граф неоднозначным, и поиск переходов молчал бы.
        return value


class DefinitionCreate(BaseModel):
    """Тело запроса создания дефинишена."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    version: int = Field(default=1, ge=1)
    definition: WorkflowDefinitionSchema
    is_active: bool = False


class DefinitionUpdate(BaseModel):
    """Тело запроса обновления дефинишена."""

    model_config = ConfigDict(extra="forbid")

    definition: WorkflowDefinitionSchema
    is_active: bool | None = None
