"""Ищет следы порчи текста: известные бессмысленные слова и фразы.

Порча появлялась при правке через консоль: слова теряли часть букв, а
предложения переставали быть предложениями. Список ниже — не эвристика, а
конкретные случаи, найденные вычиткой. Любое новое совпадение — повод
посмотреть строку глазами.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".md", ".yml", ".yaml", ".json", ".toml", ".css"}
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", "htmlcov", ".pytest_cache"}

# Слова, которые в этом проекте встречаются только в повреждённом тексте.
SUSPECT = (
    "трить",
    "за циклам",
    "птанут",
    "смогут импортироваться",
    "для случая строка",
    "построики событий",
    "дожжна событием",
    "идёт веком",
    "наверно",
    "молчаливый проглатывание",
    "запись его вернулась",
    "передача его снова",
    "внушение внешнего ключа",
    "была б неверным",
    "отдалит откат в себя",
    "Откат восил",
    "подадается из отказа",
    "по поля механизма",
    "срабытало",
    "Сработаный счётчик",
    "без поляза",
    "в определию скановеками",
    "планированик",
    "planned_for или ся",
    "невыкоменных запусках",
    "считала бы считала",
    "возобнодим дайже",
    "было недельзя",
    "Истеранение",
    "дождалось влет",
    "раньше увидит тысячи",
    "Затрус",
    "отставшемя батьях",
    "Заголвок",
    "собрет все события",
    "отдалт",
    "в поведения аварии",
    "Аренда удачена",
    "скалькой",
    "десятько",
    "стоало недельзя",
    "Цикл оборваны",
    "берёт его сразу",
    "Итог утечённое",
    "в себя успешно не был",
    "разовязанная",
    "подстаневшего уходящего вокна",
    "недействующем статусе",
    "набытый жизный цикл",
    "заблишена",
    "бюгаудя",
    "неколькими разами",
    "поведает что именно",
    "приводки",
    "вродены в несе",
    "Исчерпани проходов",
    "Показать нельзя сюже",
    "обрращения",
    "упавшая панель снову",
    "ещё не собрани",
    "разнешение само о себе",
    "понятия, которая предоставляется",
    "дождаться отдельно",
    "следищий ответ",
    "событие события",
    "атомарном влёте",
)

# Строки, которые не должны появляться в пользовательском тексте.
USER_VISIBLE_SUSPECT = ("Исчерпани проходов", "Показать нельзя сюже", "обрращения")

WORD = re.compile("|".join(re.escape(word) for word in SUSPECT))


def _text_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix in TEXT_SUFFIXES
        and not any(part in SKIP_DIRS for part in path.parts)
        and "test_repository_text" not in path.name
    ]


def test_repository_comments_are_not_corrupted() -> None:
    offenders: list[str] = []
    for path in _text_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if WORD.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == [], "повреждённый текст: " + ", ".join(offenders)


def test_user_visible_text_is_intact() -> None:
    # Пользователь читает эти строки, а не разработчик: испорченная подпись
    # видна сразу и стоит дороже, чем испорченный комментарий.
    offenders: list[str] = []
    for path in ROOT.joinpath("frontend/src").rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        text = path.read_text(encoding="utf-8")
        if any(word in text for word in USER_VISIBLE_SUSPECT):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], "испорченные надписи: " + ", ".join(offenders)
