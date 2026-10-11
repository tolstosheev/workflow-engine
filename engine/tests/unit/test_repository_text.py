"""Текст репозитория должен оставаться читаемым.

Проверка ловит повреждение кодировки в файлах конфигурации: текст, записанный
через консоль, один раз превратился в escape-последовательности — обратный
слэш, буква u и четыре шестнадцатеричные цифры вместо букв. В комментариях это
почти безобидно, а в имени задачи CI или в сообщении `echo` остаётся мусор,
который никто не проверял годами.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Расширения, где порча текста бросается в глаза и где она уже случалась.
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".md", ".yml", ".yaml", ".json", ".toml", ".css"}

SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", "htmlcov", ".pytest_cache"}

# Буквальный escape в исходнике всегда признак порчи: в проекте нет мест,
# где такую последовательность пишут руками.
LITERAL_ESCAPE = re.compile(r"\\u[0-9a-fA-F]{4}")
# Символ замены задаётся кодом, а не escape-последовательностью: сам тест не
# должен содержать то, что ищет.
REPLACEMENT_CHAR = chr(0xFFFD)


def _text_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in TEXT_SUFFIXES and path.is_file():
            files.append(path)
    return files


def test_repository_text_has_no_literal_unicode_escapes() -> None:
    offenders: list[str] = []
    for path in _text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:  # pragma: no cover - битый файл пойман этим же тестом
            offenders.append(f"{path.relative_to(ROOT)}: не разбирается как utf-8 ({exc})")
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if LITERAL_ESCAPE.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == [], "буквальные escape-последовательности: " + ", ".join(offenders)


def test_repository_text_has_no_replacement_characters() -> None:
    offenders: list[str] = []
    for path in _text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if REPLACEMENT_CHAR in line:
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == [], "символы замены: " + ", ".join(offenders)
