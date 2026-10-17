"""Стража: всё, что клиент просит у бэкенда, должно быть в прокси.

Дефект, из-за которого страница состояния молчала: браузер ходит в свой origin,
а прокси Vite знал только `/api`. Метрики лежат по `/metrics`, запрос уходил в
дев-сервер, тот отвечал 404, и разбор метрик принимал его за «метрик нет» —
страница показывала нули при живой системе.

Ни один тест этого не видел: юнит-тесты мокают клиент, сквозные на страницу
состояния не заходят. Стража ниже проверяет само свойство — каждый путь, который
клиент запрашивает у бэкенда, обязан быть либо под `/api`, либо перечислен в
прокси.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CLIENT = ROOT / "frontend" / "src" / "api" / "client.ts"
VITE = ROOT / "frontend" / "vite.config.ts"

#: Обращения клиента к бэкенду. Их два вида: `request('/api/…')` — обычные
#: ручки, и `api('/…')` — обёртка, применённая там, где ответ не JSON
#: (`/metrics` отдаёт текст). Раньше страж смотрел только на второй и потому
#: пропустил бы всё остальное: проверка, которая видит один вызов из
#: полутора десятков, выглядит работающей.
CALL = re.compile(r"(?:api|request)(?:<[^>]*>)?\(\s*['\"`](/[^'\"`]*)['\"`]")
PROXIED = re.compile(r"PROXIED_PATHS\s*=\s*\[(.*?)\]", re.S)
ENTRY = re.compile(r"['\"](/[^'\"]*)['\"]")


def _proxied() -> set[str]:
    text = VITE.read_text(encoding="utf-8")
    match = PROXIED.search(text)
    assert match is not None, "в vite.config.ts нет списка PROXIED_PATHS"
    return set(ENTRY.findall(match.group(1)))


def _requested() -> set[str]:
    return set(CALL.findall(CLIENT.read_text(encoding="utf-8")))


def test_client_requests_something() -> None:
    """Охрана от vacuous: разбор пуст — это «проверять нечего», а не «всё в порядке»."""
    requested = _requested()
    assert len(requested) >= 2, f"из клиента разобрано {len(requested)} путей — страж молчит"


def test_every_backend_path_is_proxied() -> None:
    """Ключевая проверка: путь вне прокси означает 404 от дев-сервера."""
    proxied = _proxied()
    missing = sorted(path for path in _requested() if not _is_covered(path, proxied))
    assert not missing, (
        "клиент запрашивает пути, которых нет в прокси vite: "
        f"{missing}. Браузер пойдёт по своему origin и получит 404 от "
        "дев-сервера, а не от бэкенда — по нему это читается как «сервис молчит»"
    )


def test_api_prefix_is_always_proxied() -> None:
    """Весь `/api` обязан быть в прокси: иначе сломается первая же ручка."""
    assert "/api" in _proxied(), "/api обязан быть в прокси: это весь REST-слой"


def test_metrics_is_proxied() -> None:
    """Явная проверка места, где дефект и жил.

    Отдельным тестом, а не только общим правилом: путь лежит вне `/api`, и его
    легко не заметить при чтении общего списка.
    """
    assert "/metrics" in _proxied(), (
        "метрики лежат по /metrics, вне /api: без них страница состояния "
        "показывает нули при живой системе"
    )


def _is_covered(path: str, proxied: set[str]) -> bool:
    """Путь покрыт, если совпадает с записью прокси или лежит под ней.

    Кромка нужна для `/api/...`: сам `/api` покрывает всё под ним.
    """
    return any(path == entry or path.startswith(f"{entry}/") for entry in proxied)
