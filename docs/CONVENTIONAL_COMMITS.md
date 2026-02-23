# Conventional Commits

В проекте используются [Conventional Commits](https://www.conventionalcommits.org/), чтобы по коммитам автоматически собирать блок **«Изменения по коммитам»** в GitHub Release при пуше тега.

## Формат

```
<type>(<scope>): <description>

[optional body]
[optional footer]
```

- **type** — тип изменения (обязательно).
- **scope** — область (опционально): модуль, компонент.
- **description** — краткое описание в повелительном наклонении («добавить», «исправить», не «добавлено»).

## Типы (как попадают в релиз)

| Тип        | Секция в релизе   | Примеры |
|------------|-------------------|--------|
| `feat`     | Added             | Новая возможность |
| `fix`      | Fixed             | Исправление бага |
| `docs`     | Documentation     | Только документация |
| `chore`    | Chore             | Сборка, зависимости, конфиг |
| `refactor` | Refactor          | Рефакторинг без смены поведения |
| `test`     | Tests             | Тесты |
| `style`    | Style             | Стиль кода (пробелы, форматирование) |
| `perf`     | Performance       | Оптимизация производительности |

Коммиты без префикса типа попадают в секцию **Other**.

## Примеры

```text
feat(telegram): streaming ответов в long polling
fix(security): проверка whitelist перед выполнением skill
docs: обновить README по установке
chore(deps): обновить redis до 7.x
feat(api): добавить эндпоинт /health
```

## Релизы

При пуше тега `v*` (например `v0.2.3`) workflow **Release**:

1. Строит список коммитов между предыдущим тегом и текущим.
2. Группирует их по типу и добавляет блок «Изменения по коммитам» в тело релиза.
3. Подставляет шаблон из `.github/releases/vX.Y.Z.md`, если он есть.
4. Создаёт GitHub Release с итоговым телом.

Подробнее: `.github/releases/README.md`, workflow `.github/workflows/release.yml`.
