"""Семантическая валидация графа дефинишена.

Структурная валидация в Pydantic проверяет форму; здесь проверяется смысл:
разрешимость переходов, существование компенсаций, отсутствие путей, ведущих
в никуда, и достижимость всех шагов от `start`.

Валидация собирает **все** проблемы сразу, а не падает на первой: дефинишен
с пятью ошибками должен быть отвергнут с полным списком, иначе правка идёт
по одному за прогон.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from app.handlers.registry import is_handler_importable
from app.schemas.api import ValidationIssue, ValidationResult
from app.schemas.definition import ForkStep, JoinStep, Step, WorkflowDefinitionSchema


@dataclass
class _Collector:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    def error(self, code: str, message: str, step: str | None = None) -> None:
        self.errors.append(ValidationIssue(code=code, message=message, step=step))

    def warn(self, code: str, message: str, step: str | None = None) -> None:
        self.warnings.append(ValidationIssue(code=code, message=message, step=step))


def _transitions(step: Step) -> Iterator[str]:
    """Все шаги, в которые ведёт `step`.

    У fork-шага это начала ветвей: иначе проверка достижимости не увидела бы
    параллельные пути и сочла ветки недостижимыми.
    """
    if isinstance(step, ForkStep):
        for branch_steps in step.fork.values():
            yield from branch_steps
        return

    target = step.next
    if isinstance(target, str):
        yield target
    elif isinstance(target, dict):
        yield from target.values()


def _validate_references(definition: WorkflowDefinitionSchema, collector: _Collector) -> None:
    known = set(definition.steps)

    for name, step in definition.steps.items():
        for target in _transitions(step):
            if target not in known:
                collector.error(
                    "unresolved_transition",
                    f"шаг {name!r}: переход указывает на неизвестный шаг {target!r} — "
                    f"такого шага в определении нет",
                    name,
                )

        if step.next is None and not isinstance(step, (ForkStep, JoinStep)):
            collector.warn(
                "terminal_step",
                f"шаг {name!r} не объявляет перехода — он считается последним в ветке",
                name,
            )

        if isinstance(step, JoinStep):
            for source in step.join:
                if source not in known:
                    collector.error(
                        "unresolved_join",
                        f"шаг {name!r}: список join содержит неизвестный шаг {source!r}",
                        name,
                    )

        if step.compensation and not is_handler_importable(step.compensation):
            collector.error(
                "unresolvable_compensation",
                f"шаг {name!r}: компенсация {step.compensation!r} не существует "
                f"или не импортируется",
                name,
            )

        if not is_handler_importable(step.action):
            collector.error(
                "unresolvable_action",
                f"шаг {name!r}: обработчик {step.action!r} не существует или не импортируется",
                name,
            )


def _validate_start(definition: WorkflowDefinitionSchema, collector: _Collector) -> None:
    if definition.start not in definition.steps:
        collector.error(
            "unresolved_start",
            f"поле 'start' ссылается на неизвестный шаг {definition.start!r} — "
            f"такого шага в определении нет",
        )


def _validate_reachability(definition: WorkflowDefinitionSchema, collector: _Collector) -> None:
    """Каждый шаг должен быть достижим от `start`.

    Недостижимый шаг — это мёртвый код процесса: он не выполнится, но о нём
    будут знать и валидатор, и читатель дефинишена.
    """
    reachable: set[str] = set()
    stack = [definition.start]

    while stack:
        current = stack.pop()
        if current in reachable or current not in definition.steps:
            continue
        reachable.add(current)
        stack.extend(_transitions(definition.steps[current]))

    for name in definition.steps:
        if name not in reachable:
            collector.error(
                "unreachable_step",
                f"шаг {name!r} недостижим от 'start' — он никогда не выполнится",
                name,
            )


def _find_cycles(definition: WorkflowDefinitionSchema) -> Iterator[list[str]]:
    """Возвращает циклы графа как последовательности имён шагов.

    Три цвета: не посещён, серый (в текущем стеке обхода), чёрный (обработан).
    Серый сутендь всегда означает роль:
    """
    UNVISITED, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {}
    stack: list[str] = []

    def successors_of(name: str) -> list[str]:
        # Члены join — входящие связи (join ждёт их), а не исходящие: включение
        # их сюда превращало бы любой fork/join в цикл.
        return list(_transitions(definition.steps[name]))

    def visit(name: str) -> Iterator[list[str]]:
        colour[name] = GREY
        stack.append(name)

        for successor in successors_of(name):
            if successor not in definition.steps:
                continue
            state = colour.get(successor, UNVISITED)
            if state == GREY:
                yield [*stack[stack.index(successor) :], successor]
            elif state == UNVISITED:
                yield from visit(successor)

        stack.pop()
        colour[name] = BLACK

    for name in definition.steps:
        if colour.get(name, UNVISITED) == UNVISITED:
            yield from visit(name)


def _validate_loops(definition: WorkflowDefinitionSchema, collector: _Collector) -> None:
    """Цикл допустим только с явным `loop: true` и пределом итераций.

    Цикл без счётчика — это способ зависнуть навсегда: ни интерпретатор, ни
    оператор не смогут сказать, когда процесс остановить.
    """
    for cycle in _find_cycles(definition):
        # Последний элемент цикла повторяет первый — для сообщения он лишний.
        cycle_steps = cycle[:-1]
        if not any(definition.steps[name].loop for name in cycle_steps):
            collector.error(
                "cycle_without_loop",
                f"цикл по шагам {sorted(cycle_steps)!r} не объявлен через loop: true — "
                f"процесс может не завершиться никогда",
                cycle_steps[0],
            )

    for name, step in definition.steps.items():
        if step.loop and step.max_iterations is None:
            collector.error(
                "loop_without_bound",
                f"шаг {name!r}: цикл без максимального числа итераций — "
                f"процесс не завершится никогда",
                name,
            )


def _validate_compensation_coverage(
    definition: WorkflowDefinitionSchema, collector: _Collector
) -> None:
    """Шаг без компенсации — предупреждение, а не ошибка.

    Валидация бессильна и шаги вроде подтверждения получения обратимы по
    умолчанию. Но у платёжного шага отсутствие компенсации обнаружится только
    при откате, когда деньги уже списаны, — об этом стоит сказать заранее.
    """
    for name, step in definition.steps.items():
        if step.compensation is None and not isinstance(step, (ForkStep, JoinStep)):
            collector.warn(
                "missing_compensation",
                f"шаг {name!r} не имеет компенсации — при откате он выполнит "
                f"побочные эффекты безвозвратно",
                name,
            )


def _validate_execution_support(
    definition: WorkflowDefinitionSchema, collector: _Collector
) -> None:
    """Отвергает конструкции, которые движок не исполняет.

    Параллельные ветки описаны в схеме, но исполнить их движок не умеет:
    lease захватывает инстанс целиком, а не шаг, поэтому две ветви одновременно
    в одном процессе не поместятся. Пока это так, определение с `fork` или
    `join` отвергается — при сохранении, а не часом позже при запуске.

    Молча пропустить проверку было бы хуже отказа: процесс выполнил бы только
    префикс графа и отчитался об успехе, а определение создавало бы ложное
    впечатление работающей поддержки. Решение и его цена — ADR-0008.
    """
    for name, step in definition.steps.items():
        if isinstance(step, ForkStep):
            collector.error(
                "fork_not_executed",
                f"шаг {name!r}: параллельные ветки (fork) объявлены, но не исполняются — "
                f"движок обрабатывает один шаг за раз. Оставьте шаг обычным "
                f"или разбейте процесс на два определения.",
                name,
            )
        elif isinstance(step, JoinStep):
            collector.error(
                "join_not_executed",
                f"шаг {name!r}: слияние веток (join) объявлено, но не исполняется — "
                f"слияния параллельных веток в движке нет. Уберите join.",
                name,
            )


def validate_definition(definition: WorkflowDefinitionSchema) -> ValidationResult:
    """Проверяет дефинишен и возвращает все найденные проблемы."""
    collector = _Collector()

    _validate_start(definition, collector)
    _validate_references(definition, collector)
    _validate_execution_support(definition, collector)
    _validate_reachability(definition, collector)
    _validate_loops(definition, collector)
    _validate_compensation_coverage(definition, collector)

    return ValidationResult(
        valid=not collector.errors,
        errors=collector.errors,
        warnings=collector.warnings,
    )


def collect_step_names(definition: WorkflowDefinitionSchema) -> Iterable[str]:
    """Имена шагов в порядке объявления — для краткой карточки в API."""
    return definition.steps.keys()
