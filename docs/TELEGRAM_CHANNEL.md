# Канал Telegram

Описание реализации канала Telegram в проекте и ориентиры по структуре (по мотивам [OpenClaw Telegram](https://github.com/openclaw/openclaw/blob/main/extensions/telegram/src/channel.ts)).

---

## Текущая реализация (Python)

- **Файл:** `assistant/channels/telegram.py`
- **Транспорт:** long polling (`getUpdates`), без webhook.
- **Конфиг:** один бот (токен и список разрешённых user_id из Redis/дашборда или env).
- **События:** входящие сообщения → `IncomingMessage` в шину; исходящие `OutgoingReply` / стрим `StreamToken` → отправка в чат.
- **Разметка:** исходящий текст конвертируется в Telegram HTML (`**жирный**` → `<b>`, `\`код\`` → `<code>`, блоки кода → `<pre>`), чтобы в чате не отображались «сырые» знаки.
- **Безопасность:** whitelist по `user_id`, rate limit на пользователя, pairing по коду из дашборда.
- **MCP:** запросы подтверждения (кнопки Подтвердить/Отклонить) и уведомления уходят в тот же чат через шину.

### Разделы по обязанностям (как в OpenClaw)

| Область | У нас | OpenClaw (reference) |
|--------|--------|------------------------|
| **Pairing** | Код из дашборда, `/start CODE`, глобальный режим pairing | `notifyApproval`, `normalizeAllowEntry`, idLabel `telegramUserId` |
| **Outbound** | Одно сообщение или обрезка по 4096; stream — одно сообщение с edit | `chunker` (chunkMarkdownText), `textChunkLimit: 4000`, несколько сообщений при длинном тексте |
| **Reply/Thread** | `reply_to_message_id` из payload | `replyToMessageId`, `messageThreadId` (топики в супергруппах) |
| **Status / Probe** | Дашборд: «Проверить бота» (getMe) | `probeAccount`, `probeTelegram(token, timeout)` |
| **Config** | Один аккаунт, Redis/env | Мультиаккаунт, `listAccountIds`, `resolveAccount`, `isConfigured` |
| **Security** | allowlist (allowed_user_ids), rate limit | `resolveDmPolicy`, `allowFrom`, `groupPolicy`, `collectWarnings` |
| **Capabilities** | Текст, inline-кнопки | Текст, медиа, опросы, топики, реакции |

---

## Возможные улучшения в рамках проекта

1. **Chunker длинных сообщений**  
   При длине текста > 4000 символов не обрезать, а резать по границам (например, по строкам) и отправлять несколько сообщений подряд (как в OpenClaw: `textChunkLimit: 4000`, `chunker`).

2. **Probe как единая функция**  
   Вынести проверку бота (getMe) в `probe_telegram(token, timeout)` и использовать её в дашборде и при старте адаптера для единообразного статуса.

3. **Reply и thread_id**  
   Уже передаём `reply_to_message_id`. При появлении топиков (супергруппы) можно добавить `message_thread_id` в payload и в sendMessage.

4. **Команды репо (9.2)**  
   `/repos`, `/github`, `/gitlab`: список и поиск репо с inline-кнопками и пагинацией «назад»/«вперёд».

5. **Медиа и опросы**  
   При необходимости — отправка фото/документов и опросов через тот же канал (отдельные обработчики outbound).

---

## Ссылки

- [OpenClaw Telegram channel.ts](https://github.com/openclaw/openclaw/blob/main/extensions/telegram/src/channel.ts) — плагин канала: pairing, outbound (chunker, sendText, sendMedia, sendPoll), status, gateway (webhook/polling).
- [Telegram Bot API](https://core.telegram.org/bots/api) — sendMessage, parse_mode HTML, getUpdates, inline_keyboard.
