"""Куда смотрит сквозная проверка по умолчанию.

Регрессия из подготовки к защите: адрес был зашит в скрипт как `localhost:8000`,
а порт публикации задаётся в `.env`. На машине, где 8000 занят соседним
проектом, `make smoke` без адреса уезжал на чужой стенд — и это хуже падения:
проверка могла пройти по чужому ответу.
"""

from __future__ import annotations

import pytest

from scripts.smoke import default_base_url


async def test_default_target_follows_the_configured_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "8123")

    assert default_base_url() == "http://localhost:8123"


async def test_default_target_stays_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    # Порт меняется, имя узла — нет: адрес стенда всегда локальный, и правка
    # порта в `.env` не должна уводить проверку на другую машину.
    monkeypatch.setenv("API_PORT", "8123")

    assert default_base_url().startswith("http://localhost:")
