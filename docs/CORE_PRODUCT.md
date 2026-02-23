# Ядро продукта (0.2.x)

Один документ о том, что входит в **ядро** ассистента, что **опционально**, и как запустить **минимальный контур** без лишних зависимостей.

---

## Ядро (обязательный минимум)

Под «ядром» понимается набор компонентов, достаточный для работы персонального ассистента: диалог в Telegram, ИИ (локальная или облачная модель), задачи и краткосрочная память.

| Компонент | Назначение |
|-----------|------------|
| **Redis** | Очередь событий (Event Bus), конфиг из дашборда, short-term/summary память, задачи. |
| **assistant-core** | Оркестратор, агенты (Assistant, Tool), скиллы (filesystem, shell, git, tasks, memory_control, file_ref и др.), модель (Ollama/OpenAI-совместимый API). |
| **telegram-adapter** | Long polling, приём сообщений, отправка ответов и стриминг, подписка на `assistant:outgoing_reply`. |
| **Память (ядро)** | Short-term (последние N сообщений), summary (сжатие), task memory (задачи). Без векторной памяти ядро работает. |
| **Модель** | Один провайдер (локальный или облачный); настройка через дашборд (URL, API key, имя модели). |

Без дашборда конфиг можно задавать через Redis/env/YAML; для удобства настройки бота, модели и MCP рекомендуется **dashboard**.

---

## Опциональные модули

| Модуль | Зависимости (extra) | Назначение |
|--------|----------------------|------------|
| **Векторная память / RAG** | `vector` (sentence-transformers) | Поиск по эмбеддингам, vector_rag skill, долговременная векторная память. Без установки `.[vector]` RAG возвращает пустые результаты. |
| **Dashboard** | `dashboard` (flask) | Web-интерфейс: Telegram (токен, pairing, Chat ID для MCP), модель, MCP endpoints, мониторинг Redis. |
| **Индексация файлов** | `files`, `archives`, `ocr` | PDF, DOCX, XLSX, архивы, OCR для картинок; индексация репо и документов в Qdrant. |
| **Qdrant** | (внешний сервис) | Векторная БД для документов и, опционально, памяти. Настраивается через дашборд (Данные) или env. |
| **Email** | (нет отдельного extra) | Канал отправки писем, скилл send_email; настройка SMTP/SendGrid в дашборде. |
| **MCP-сервер (агент)** | Встроен в dashboard | HTTP API для notify/confirmation/replies; подключается любой ИИ (Cursor и др.). |

---

## Установка: минимальный vs полный

- **Минимальный контур (без векторной памяти):**  
  `pip install .`  
  Устанавливаются только базовые зависимости (redis, httpx, openai, pydantic, pyyaml). Запуск core и telegram-adapter возможен; vector_rag и индексация в Qdrant не будут иметь эмбеддингов (RAG вернёт пустое или ошибку при отсутствии библиотеки).

- **С векторной памятью:**  
  `pip install .[vector]`

- **С дашбордом:**  
  `pip install .[dashboard]` (или `.[vector,dashboard]` для полного сценария с RAG).

- **Полный набор (как в Docker):**  
  В Docker используется `docker/requirements.txt`, в нём перечислены и sentence-transformers, и парсеры файлов. Локально аналог: `pip install .[vector,dashboard,files,archives,ocr]` (если все такие группы есть в pyproject.toml).

---

## Запуск минимального контура

1. **Redis** должен быть доступен (`REDIS_URL` или по умолчанию `redis://localhost:6379/0`).

2. **Запуск ядра и Telegram-адаптера (без дашборда):**
   ```bash
   # Терминал 1
   python -m assistant.main

   # Терминал 2
   python -m assistant.channels.telegram
   ```
   Конфиг (токен бота, URL модели, разрешённые user_id) берётся из Redis и env. Если дашборд не запускается, настройки нужно внести в Redis вручную или через env (см. контракт ключей в docs и config_store).

3. **С дашбордом (рекомендуется для настройки):**
   ```bash
   # Запустить Redis, затем:
   pip install .[dashboard]
   # Терминал 1 — дашборд
   python -m assistant.dashboard.app  # или gunicorn
   # Терминал 2 — core
   python -m assistant.main
   # Терминал 3 — telegram-adapter
   python -m assistant.channels.telegram
   ```

4. **MCP (опционально):** при запущенном дашборде MCP HTTP API доступен по адресу дашборда (см. [MCP_DEV_SERVER.md](MCP_DEV_SERVER.md)). Отдельный процесс для MCP не нужен.

---

## Итог

- **Ядро:** Redis + assistant-core + telegram-adapter + память (short_term, summary, задачи) + модель.  
- **Опционально:** vector (RAG), dashboard, индексация файлов, Qdrant, email, MCP HTTP API.  
- **Минимальная установка:** `pip install .`; для RAG — `pip install .[vector]`; для веб-настройки — `pip install .[dashboard]`.

Дорожная карта развития ядра и опциональных фич: [ROADMAP_0.2.1.md](ROADMAP_0.2.1.md), [ANALYTICS_AND_ROADMAP_2026.md](ANALYTICS_AND_ROADMAP_2026.md).
