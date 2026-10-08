# Диаграммы

Исходники Mermaid-схем. Каждая схема хранится отдельным файлом и вставляется
в документ блоком ` ```mermaid ` — так она остаётся редактируемой и не
расходится с кодом.

| Файл | Схема | Используется в |
|---|---|---|
| [system-context.mmd](system-context.mmd) | Контекстная диаграмма системы | [`../ARCHITECTURE.md`](../ARCHITECTURE.md) |
| [components.mmd](components.mmd) | Подсистемы и их взаимодействия | [`../ARCHITECTURE.md`](../ARCHITECTURE.md) |
| [execution-sequence.mmd](execution-sequence.mmd) | Последовательность исполнения шага | [`../ENGINE.md`](../ENGINE.md) |
| [recovery-sequence.mmd](recovery-sequence.mmd) | Падение воркера и восстановление | [`../ENGINE.md`](../ENGINE.md) |
| [compensation-sequence.mmd](compensation-sequence.mmd) | Откат по Saga | [`../ENGINE.md`](../ENGINE.md) |
| [instance-states.mmd](instance-states.mmd) | Машина состояний инстанса | [`../ENGINE.md`](../ENGINE.md) |
| [er.mmd](er.mmd) | ER-диаграмма | [`../DATA-MODEL.md`](../DATA-MODEL.md) |

## Правила оформления

- Только Mermaid. Растровые картинки допускаются только для схем, которые
  Mermaid не выражает (например, диаграмма развёртывания с ресурсами).
- Идентификаторы узлов латиницей, подписи — по-русски.
- Диаграмма отражает текущее состояние кода. Схема, расходящаяся с кодом,
  хуже отсутствующей схемы.
- Цвета не задаются вручную: они мешают читать diff и зависят от темы.