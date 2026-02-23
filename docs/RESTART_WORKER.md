# Воркер перезапуска (ROADMAP F)

По команде `/restart` в Telegram (для пользователей из TELEGRAM_ADMIN_IDS) в Redis записывается ключ `assistant:action:restart_requested`. Воркер следит за этим ключом и выполняет команду перезапуска (например `docker compose restart`).

## Запуск

```bash
# Постоянный цикл (опрос каждые 10 с)
python scripts/restart_worker.py

# Один проход (для cron)
python scripts/restart_worker.py --once
```

## Переменные окружения

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `REDIS_URL` | `redis://localhost:6379/0` | Подключение к Redis |
| `RESTART_CMD` | `docker compose restart` | Команда для перезапуска |
| `RESTART_POLL_SEC` | `10` | Интервал опроса (секунды) |

Для «сухого» режима (только логировать запрос и удалять ключ, не выполнять команду) задайте пустое значение: `RESTART_CMD=`.

## Пример для cron

Раз в минуту проверять флаг и при наличии выполнять перезапуск:

```cron
* * * * * REDIS_URL=redis://localhost:6379/0 RESTART_CMD="docker compose -f /path/to/docker-compose.yml restart" python /path/to/assistent_core/scripts/restart_worker.py --once
```

## Безопасность

- Запускайте воркер с минимальными правами, достаточными только для выполнения RESTART_CMD.
- Не давайте процессу доступ к секретам приложения, если в этом нет необходимости.
- Аудит: в логах воркера фиксируются `user_id` и `timestamp` из Redis при каждом запросе на перезапуск.
