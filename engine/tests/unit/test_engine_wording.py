"""Язык сообщений движка.

Надписи интерфейса приходят из движка: текст ошибки API или подпись метрики
показывается человеку дословно. Значит словарь `docs/UI-GLOSSARY.md` относится и
к сообщениям здесь — иначе на экране словарь соблюдается, а в сообщении «шаг не
выполнен» приходит «инстанс».

Проверка читает исходники без комментариев и docstring'ов: они объясняют
решение разработчику и остаются на языке проекта, а всё остальное — строки —
рано или поздно попадает человеку в API, в журнал или на экран.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "engine" / "app"

#: Слово, которого в сообщениях быть не должно, и чем оно заменено.
FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("инстанс", "процесс заказа"),
    ("откат", "компенсация"),
    ("откатан", "компенсация"),
    ("воркер", "исполнитель"),
    ("аренд", "закрепление"),
    ("дефинишен", "схема процесса"),
    ("инъекц", "внесение неисправностей"),
)


def _sources() -> list[Path]:
    return sorted(path for path in APP.rglob("*.py"))


def _string_literals(path: Path) -> list[tuple[int, str]]:
    """Строковые литералы модуля, кроме docstring'ов.

    Разбор идёт деревом, а не регулярным выражением: регулярное выражение
    отличает не умеет, а ошибка здесь стоит слова в сообщении.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            found.append((node.lineno, node.value))
    return found


@pytest.mark.parametrize("path", _sources(), ids=lambda item: item.name)
def test_messages_do_not_use_internal_words(path: Path) -> None:
    offenders: list[str] = []

    for line, text in _string_literals(path):
        low = text.lower()
        for word, replacement in FORBIDDEN:
            if word in low:
                offenders.append(f"{path.name}:{line}: «{word}» → {replacement}: {text[:60]}")

    assert offenders == [], "слова вне словаря:\n" + "\n".join(offenders)


def test_the_check_reads_the_sources() -> None:
    # Страж, не читающий файлы, был бы зелёным всегда: пустой набор находок
    # проходит проверку «слов нет». Здесь утверждается, что модули и строки
    # действительно находятся.
    literal_count = sum(len(_string_literals(path)) for path in _sources())

    assert len(_sources()) > 20
    assert literal_count > 100
