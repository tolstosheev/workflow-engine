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

Реализация: ADR-0001 — в Фазе 0, ADR-0002…0006 — в Фазе 1 (схема данных и
валидация дефинишена), ADR-0002…0005 в объёме ядра — в Фазе 2, ADR-0007 — в
Фазе 5 (замеры и методика их проведения).