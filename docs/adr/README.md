# ADR

Architecture Decision Records — принятые архитектурные решения и причины,
по которым они приняты.

Формат — Nygard: Context / Decision / Consequences (Positive / Negative /
Neutral) / Alternatives considered / References. Раздел **Negative** обязателен
и не может быть пустым: решение без видимой цены не бывает честным.

## Статусы

| Статус | Значение |
|---|---|
| `Proposed` | обсуждается, код ещё не написан |
| `Accepted` | принято и реализуется |
| `Superseded by ADR-XXX` | заменено более поздним решением |

Принятый ADR неизменяем. Новый контекст — новый ADR, в старом меняется только
строка `Status`.

## Реестр

| ADR | Тема | Статус |
|---|---|---|
| [0001](0001-stek-tehnologii.md) | Выбор стека технологий | Accepted |
| [0002](0002-zhurnal-sobytiy-vmesto-dolgoy-tranzakcii.md) | Журнал событий вместо долгой транзакции БД | Accepted |
| [0003](0003-lease-vmesto-raspredelennyh-blokirovok.md) | Lease вместо распределённых блокировок | Accepted |
| [0004](0004-saga-vmesto-dvukhfaznoy-commit.md) | Saga с компенсациями вместо 2PC | Accepted |
| [0005](0005-at-least-once-i-idempotentnost.md) | At-least-once доставка и идемпотентность | Accepted |
| [0006](0006-format-opredeleniya-workflow.md) | Формат определения workflow | Accepted |
| [0007](0007-metodika-zmerov.md) | Методика измерения производительности | Accepted |
| [0008](0008-parallelnye-vetki-otkaz.md) | Параллельные ветки: честный отказ вместо заявленной поддержки | Accepted |
| [0009](0009-povtoryaemost-oshibok.md) | Повторяемость ошибки объявляет обработчик | Accepted |
| [0010](0010-dva-sloya-idempotentnosti.md) | Два слоя идемпотентности: обработчик и движок | Accepted |
| [0011](0011-otkaz-ne-raven-otmene.md) | Отказ без компенсации отличается от отмены | Accepted |
| [0012](0012-metriki-v-multiprocess-rezhime.md) | Метрики в multiprocess-режиме Prometheus | Accepted |
| [0013](0013-belyy-spisok-obrabotchikov.md) | Обработчики ограничены белым списком модулей | Accepted |

Реализация: ADR-0001 — в Фазе 0, ADR-0002…0006 — в Фазе 1 (схема данных и
валидация дефинишена), ADR-0002…0005 в объёме ядра — в Фазе 2, ADR-0007 — в
Фазе 5 (замеры и методика их проведения), ADR-0008 — в Фазе 7, ADR-0009…0011 — в релизе 1.2.1 (исправления дефектов отката и повторов, обнаружение повторов и различие отказа с отменой), ADR-0012 — при разборе стенда: метрики воркера не достигали `/metrics`.
