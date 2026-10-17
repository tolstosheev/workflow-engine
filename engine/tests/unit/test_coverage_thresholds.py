"""Порог покрытия один, и документация не имеет права о нём забывать.

Число в `pyproject.toml`, флаги в `Makefile` и фразы в документации — это три
записи одного факта. Они и разошлись: в акте внедрения стояло покрытие старого
прогона при старом пороге, порог фронтенда в `DEVELOPMENT.md` не совпадал с
`vite.config.ts`, а число «96 %» в фазе 0 читалось как требование, хотя это факт
того прогона.

Проверка требует, чтобы любое число, названное порогом, совпадало с числом из
конфигурации — движка или фронтенда, — и чтобы порог движка не опустился ниже
90 %: понизить его, чтобы позеленело, запрещено (§10 `AGENTS.md`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = ROOT / "engine" / "pyproject.toml"
MAKEFILE = ROOT / "Makefile"
VITE_CONFIG = ROOT / "frontend" / "vite.config.ts"

#: Документы, где называется порог покрытия.
DOCUMENTS = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/DEVELOPMENT.md",
    "docs/GIT_WORKFLOW.md",
    "docs/DEPLOYMENT-ACT.md",
)

#: «порог покрытия 90%», «при пороге 90%», `fail_under = 90`, `--cov-fail-under=90`.
THRESHOLD_MENTIONS = (
    re.compile(r"порог\w*\s+(?:покрытия\s+|покрыти\w*\s+)?(\d+)\s*%"),
    re.compile(r"при пороге\s+(\d+)\s*%"),
    re.compile(r"fail_under\s*=\s*(\d+)"),
    re.compile(r"--cov-fail-under=(\d+)"),
)


def engine_threshold() -> int:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r"^fail_under\s*=\s*(\d+)", text, re.MULTILINE)
    assert match is not None, "в pyproject нет fail_under"
    return int(match.group(1))


def frontend_thresholds() -> dict[str, int]:
    text = VITE_CONFIG.read_text(encoding="utf-8")
    block = re.search(r"thresholds:\s*\{(?P<body>[^}]*)\}", text)
    assert block is not None, "в vite.config.ts нет thresholds"
    found = re.findall(r"(statements|branches|functions|lines):\s*(\d+)", block.group("body"))
    assert len(found) == 4, f"в thresholds не четыре величины: {found}"
    return {name: int(value) for name, value in found}


def named_thresholds(document: str) -> set[int]:
    text = (ROOT / document).read_text(encoding="utf-8")
    return {int(number) for pattern in THRESHOLD_MENTIONS for number in pattern.findall(text)}


def test_engine_threshold_is_not_lowered() -> None:
    assert engine_threshold() >= 90


@pytest.mark.parametrize(
    "flag", re.findall(r"--cov-fail-under=(\d+)", MAKEFILE.read_text(encoding="utf-8"))
)
def test_makefile_uses_the_same_threshold(flag: str) -> None:
    assert int(flag) == engine_threshold()


@pytest.mark.parametrize("document", DOCUMENTS)
def test_documents_name_a_configured_threshold(document: str) -> None:
    configured = {engine_threshold(), *frontend_thresholds().values()}
    named = named_thresholds(document)

    assert named <= configured, (
        f"{document} называет порогом {sorted(named - configured)}, "
        f"а в конфигурации {sorted(configured)}"
    )


def test_engine_documents_name_the_engine_threshold() -> None:
    # У движка и фронтенда числа свои, и в акте внедрения стоят рядом: без разбора
    # строк строка о фронтенде сочла бы движковый порог расхождением.
    engine_documents = (
        "AGENTS.md",
        "CONTRIBUTING.md",
        "docs/DEVELOPMENT.md",
        "docs/GIT_WORKFLOW.md",
    )
    stray = {
        document: sorted(named_thresholds(document) - {engine_threshold()})
        for document in engine_documents
    }
    stray = {document: numbers for document, numbers in stray.items() if numbers}

    assert not stray, f"названо не число движка: {stray}"


def test_frontend_thresholds_in_development_are_current() -> None:
    text = (ROOT / "docs" / "DEVELOPMENT.md").read_text(encoding="utf-8")
    tuples = re.findall(r"(\d+)/(\d+)/(\d+)/(\d+)", text)
    actual = frontend_thresholds()
    expected = (
        actual["statements"],
        actual["branches"],
        actual["functions"],
        actual["lines"],
    )

    assert tuples, "в DEVELOPMENT.md нет четвёрки порогов фронтенда"
    for group in tuples:
        assert tuple(int(value) for value in group) == expected, (
            f"четвёрка {group} разошлась с vite.config.ts {expected}"
        )


def test_the_documents_are_read_at_all() -> None:
    # Страж, который не открыл ни одного файла, проходит любую проверку.
    assert all((ROOT / document).is_file() for document in DOCUMENTS)
    assert MAKEFILE.is_file() and VITE_CONFIG.is_file()
