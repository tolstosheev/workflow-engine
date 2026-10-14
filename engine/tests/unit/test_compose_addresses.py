"""Охрана адреса БД в compose.

Адрес БД внутри сети compose не выбирают: он литеральный (`db:5432`), как у
сервиса миграций. Подстановка `${DATABASE_URL}` выглядела безобидно, но compose
берёт переменную из окружения оболочки, а адрес для хостовых скриптов — это
`localhost:5434`. Заданный в оболочке, он уезжал внутрь контейнера, где такого
хоста нет: воркер уходил в бесконечные попытки соединения, оставаясь «живым»,
и инстансы висели в очереди, не показывая причины.

Проверка ловит возвращение подстановки. Она же ловит и противоположную ошибку —
`localhost` внутри контейнера, — потому что оба адреса неверны там, где стоит
`db:5432`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yml"

#: Сервисы, которым нужен адрес БД внутри сети compose.
CONSUMERS = ("api", "worker", "worker-2")


def _service_block(text: str, service: str) -> str:
    """Возвращает блок сервиса из compose-файла без строк комментариев.

    Комментарии отбрасываются намеренно: они объясняют, почему подстановки
    быть не должно, и потому содержат её дословно. Проверять надо конфигурацию,
    а не текст рядом с ней — иначе любой внятный комментарий объявляет дефектом
    сам себя.
    """
    match = re.search(rf"^  {re.escape(service)}:\n(?P<body>(?:    .*\n|\n)*)", text, re.M)
    assert match is not None, f"сервис {service!r} не найден в docker-compose.yml"
    return "\n".join(
        line for line in match.group("body").splitlines() if not line.lstrip().startswith("#")
    )


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


@pytest.mark.parametrize("service", CONSUMERS)
def test_database_url_is_not_interpolated(compose_text: str, service: str) -> None:
    """Подстановка из окружения — источник тихой поломки, её здесь быть не должно."""
    block = _service_block(compose_text, service)

    assert "${DATABASE_URL" not in block, (
        f"{service}: DATABASE_URL нельзя брать из окружения оболочки — "
        f"хостовый адрес уедет внутрь контейнера и сломает подключение"
    )


@pytest.mark.parametrize("service", CONSUMERS)
def test_database_url_points_inside_the_compose_network(compose_text: str, service: str) -> None:
    """Контейнер ходит в БД по сетевому имени, а не по имени машины."""
    block = _service_block(compose_text, service)
    urls = re.findall(r"DATABASE_URL:\s*(\S+)", block)

    assert urls, f"{service}: в блоке нет DATABASE_URL"
    for url in urls:
        assert "@db:5432/" in url, f"{service}: ожидался адрес db:5432, получено {url}"
        assert "localhost" not in url, f"{service}: localhost внутри контейнера не разрешается"


def test_migrations_service_keeps_its_own_address(compose_text: str) -> None:
    """Сервис миграций работает через синхронный драйвер и адрес свой."""
    block = _service_block(compose_text, "migrate")

    assert "SYNC_DATABASE_URL:" in block
    assert "@db:5432/" in block
