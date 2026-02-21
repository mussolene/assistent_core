# Авторизация дашборда

Дашборд использует **пользователей и сессии в Redis**; доступ к страницам — по cookie и ролям.

## Поток

1. **Первый запуск** — в Redis нет пользователей → редирект на `/setup`. Создаётся первый пользователь с ролью `owner`, создаётся сессия, выставляется cookie.
2. **Вход** — `/login`: логин + пароль, проверка через `verify_user`, создание сессии, cookie `assistant_sid`.
3. **Запросы** — декораторы `@require_auth` и `@require_role(...)` читают cookie, загружают сессию из Redis, при необходимости редирект на `/login` или `/setup`.
4. **Выход** — `/logout`: удаление сессии из Redis и cookie.

## Redis

- **assistant:users** (set) — логины зарегистрированных пользователей.
- **assistant:user:{login}** (string, JSON) — данные пользователя: `password_hash`, `salt`, `role`, `display_name`. Пароль: PBKDF2-HMAC-SHA256, 100_000 итераций.
- **assistant:session:{session_id}** (string, JSON, TTL 24h) — сессия: `{"login": "..."}`. TTL обновляется при каждом обращении.

## Роли

- **owner** — полный доступ (настройки Telegram, модели, MCP, мониторинг).
- **operator** — доступ по необходимости (можно ограничить в коде через `@require_role("owner", "operator")`).
- **viewer** — только просмотр (если маршруты помечены соответственно).

Сейчас все защищённые страницы используют `require_auth`; разграничение по ролям — через `require_role("owner")` там, где нужно.

## Cookie и SECRET_KEY

- **Cookie:** `assistant_sid` — значение = session_id. `httponly=True`, `samesite=Lax`, `secure` — при `HTTPS=1` или `FLASK_ENV=production`.
- **SECRET_KEY** (Flask) — используется Flask для подписи сессии. В коде дашборда сессия хранится в Redis по session_id, но `app.secret_key` всё равно нужен для внутренних нужд Flask. В продакшене **обязательно** задать в `.env`: `SECRET_KEY=<случайная строка>`. Иначе при использовании `session` или подписей возможны предсказуемые значения.

## Рекомендации

- В продакшене: `SECRET_KEY` уникальный, не из репозитория.
- HTTPS: задать `HTTPS=1` или `FLASK_ENV=production`, чтобы cookie уходила только по `secure`.
- Пароль при setup: не менее 6 символов; логин — не менее 2.
- Аудит: успешный/неуспешный вход и logout логируются через `assistant.security.audit` (если доступен).

## API для фронта

**GET /api/session** — без редиректа возвращает JSON: при валидной cookie — `{"logged_in": true, "login": "...", "role": "...", "display_name": "..."}`, иначе `{"logged_in": false}`. Удобно для SPA или проверки состояния входа.
