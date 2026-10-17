"""Числа для акта внедрения: модули, строки, миграции, ADR, коммиты.

Числа в `docs/DEPLOYMENT-ACT.md` год назад разошлись с кодом на треть: модулей
было 39, стало 43, миграций 8, стало 12, ADR 17, стало 23. Причина не в небрежности,
а в том, что пересчитывать их было нечем — таблица держалась руками.

Скрипт ничего не проверяет и ничего не меняет: он печатает строки, которые
подставляются в документ. Разница между его строкой и документом видна глазами.

Запуск: `make numbers` или `python -m scripts.numbers`.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "engine"


def python_files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*.py") if "__pycache__" not in path.parts)


def script_files(folder: Path) -> list[Path]:
    return sorted(
        path for path in folder.rglob("*.ts*") if not path.name.endswith((".test.ts", ".test.tsx"))
    )


def test_files(folder: Path) -> list[Path]:
    return sorted(
        path for path in folder.rglob("*.ts*") if path.name.endswith((".test.ts", ".test.tsx"))
    )


def lines_of(paths: list[Path]) -> int:
    return sum(len(path.read_text(encoding="utf-8").splitlines()) for path in paths)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    return result.stdout.strip()


def main() -> int:
    app = python_files(ENGINE / "app")
    engine_tests = python_files(ENGINE / "tests")
    frontend = script_files(ROOT / "frontend" / "src")
    frontend_tests = test_files(ROOT / "frontend" / "src")
    # Считаются сами проверки, а не файлы: `catalog.spec.ts` содержит одну
    # проверку на весь каталог, и «файлов: 3» против «проверок: 16» отвечало бы
    # на другой вопрос, чем тот, который задаёт таблица акта.
    browser_checks = sum(
        len(re.findall(r"^test\(", path.read_text(encoding="utf-8"), re.MULTILINE))
        for path in sorted((ROOT / "frontend" / "e2e").glob("*.spec.ts"))
    )
    migrations = list((ENGINE / "alembic" / "versions").glob("*.py"))
    adrs = [path for path in (ROOT / "docs" / "adr").glob("*.md") if path.name != "README.md"]

    print(f"Модулей движка: {len(app)}")
    print(f"Строк в app/: {lines_of(app)}")
    print(f"Строк в тестах движка: {lines_of(engine_tests)}")
    print(f"Строк во фронтенде (src, без тестов): {lines_of(frontend)}")
    print(f"Строк в тестах фронтенда: {lines_of(frontend_tests)}")
    print(f"Сквозных проверок в браузере: {browser_checks}")
    print(f"Миграций: {len(migrations)}")
    print(f"ADR: {len(adrs)}")
    print(f"Коммитов: {git('rev-list', '--count', 'HEAD')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
