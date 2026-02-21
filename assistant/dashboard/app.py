"""Web dashboard: настройка Telegram и подключения к модели. Redis задаётся в compose."""

from __future__ import annotations

import os
import re

from flask import Flask, request, render_template_string, redirect, url_for, flash

from assistant.dashboard.config_store import (
    get_redis_url,
    get_config_from_redis_sync,
    set_config_in_redis_sync,
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-production")

INDEX_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Assistant — Настройка</title>
  <style>
    :root { --bg: #0f0f12; --card: #1a1a1f; --text: #e4e4e7; --muted: #71717a; --accent: #22c55e; --border: #27272a; }
    * { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; min-height: 100vh; padding: 2rem; }
    .container { max-width: 560px; margin: 0 auto; }
    h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
    h2 { font-size: 1.1rem; color: var(--muted); margin: 1.5rem 0 0.75rem; }
    .sub { color: var(--muted); font-size: 0.9rem; margin-bottom: 1.5rem; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; margin-bottom: 1rem; }
    label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 0.35rem; }
    input[type="text"], input[type="password"], input[type="url"] { width: 100%; padding: 0.6rem 0.75rem; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 1rem; }
    input:focus { outline: none; border-color: var(--accent); }
    input[type="checkbox"] { width: 1rem; height: 1rem; margin-right: 0.5rem; }
    .hint { font-size: 0.8rem; color: var(--muted); margin-top: 0.25rem; }
    .row { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }
    button { background: var(--accent); color: var(--bg); border: none; padding: 0.65rem 1.25rem; border-radius: 8px; font-size: 1rem; cursor: pointer; font-weight: 600; margin-top: 0.5rem; }
    button:hover { opacity: 0.9; }
    .flash { padding: 0.75rem; border-radius: 8px; margin-bottom: 1rem; }
    .flash.success { background: rgba(34, 197, 94, 0.2); border: 1px solid var(--accent); }
    .flash.error { background: rgba(239, 68, 68, 0.2); border: 1px solid #ef4444; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Настройка ассистента</h1>
    <p class="sub">Токен бота и параметры модели сохраняются в Redis (сервис redis уже в составе compose).</p>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for cat, msg in messages %}
          <div class="flash {{ cat }}">{{ msg }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    <form method="post" action="{{ url_for('save') }}">
      <h2>Telegram</h2>
      <div class="card">
        <label for="token">Telegram Bot Token</label>
        <input id="token" name="telegram_bot_token" type="password" value="{{ config.get('TELEGRAM_BOT_TOKEN', '') }}" placeholder="123456:ABC..." autocomplete="off">
        <p class="hint">Получить у @BotFather.</p>
      </div>
      <div class="card">
        <label for="users">Разрешённые User ID (через запятую)</label>
        <input id="users" name="telegram_allowed_user_ids" type="text" value="{{ config.get('TELEGRAM_ALLOWED_USER_IDS_STR', '') }}" placeholder="123456789, 987654321">
        <p class="hint">Пусто — разрешить всех (только для разработки).</p>
      </div>

      <h2>Модель</h2>
      <div class="card">
        <label for="base_url">URL API модели (Ollama / OpenAI-совместимый)</label>
        <input id="base_url" name="openai_base_url" type="url" value="{{ config.get('OPENAI_BASE_URL', '') }}" placeholder="http://host.docker.internal:11434/v1">
        <p class="hint">Для Docker: host.docker.internal:11434. Для локального запуска: localhost:11434.</p>
      </div>
      <div class="card">
        <label for="model_name">Имя модели</label>
        <input id="model_name" name="model_name" type="text" value="{{ config.get('MODEL_NAME', '') }}" placeholder="llama3.2">
        <p class="hint">Например: llama3.2, mistral, qwen2.</p>
      </div>
      <div class="card">
        <label for="fallback_name">Имя модели fallback (облако, опционально)</label>
        <input id="fallback_name" name="model_fallback_name" type="text" value="{{ config.get('MODEL_FALLBACK_NAME', '') }}" placeholder="gpt-4">
        <p class="hint">Используется при включённом облачном fallback.</p>
      </div>
      <div class="card">
        <div class="row">
          <input type="checkbox" id="cloud" name="cloud_fallback_enabled" value="1" {{ 'checked' if config.get('CLOUD_FALLBACK_ENABLED') == 'true' else '' }}>
          <label for="cloud" style="margin-bottom:0">Включить облачный fallback (OpenAI и т.п.)</label>
        </div>
        <p class="hint">Если локальная модель недоступна — запрос уйдёт в облако (нужен API ключ).</p>
      </div>
      <div class="card">
        <label for="api_key">API ключ (облако)</label>
        <input id="api_key" name="openai_api_key" type="password" value="{{ config.get('OPENAI_API_KEY', '') }}" placeholder="sk-... или ollama" autocomplete="off">
        <p class="hint">Для Ollama можно оставить пустым или «ollama». Для облака — ключ OpenAI.</p>
      </div>

      <button type="submit">Сохранить</button>
    </form>
    <p class="sub" style="margin-top:1.5rem">После смены настроек модели перезапустите: <code>docker compose restart assistant-core</code>. После смены токена: <code>docker compose restart telegram-adapter</code>.</p>
  </div>
</body>
</html>
"""


def load_config() -> dict:
    redis_url = get_redis_url()
    data = get_config_from_redis_sync(redis_url)
    if "TELEGRAM_ALLOWED_USER_IDS" in data and isinstance(data["TELEGRAM_ALLOWED_USER_IDS"], list):
        data["TELEGRAM_ALLOWED_USER_IDS_STR"] = ",".join(str(x) for x in data["TELEGRAM_ALLOWED_USER_IDS"])
    else:
        data["TELEGRAM_ALLOWED_USER_IDS_STR"] = data.get("TELEGRAM_ALLOWED_USER_IDS", "") or ""
    return data


@app.route("/")
def index():
    config = load_config()
    return render_template_string(INDEX_HTML, config=config)


@app.route("/save", methods=["POST"])
def save():
    redis_url = get_redis_url()

    token = (request.form.get("telegram_bot_token") or "").strip()
    if not token:
        flash("Укажите токен бота.", "error")
        return redirect(url_for("index"))

    users_str = (request.form.get("telegram_allowed_user_ids") or "").strip()
    user_ids = []
    for part in re.split(r"[\s,]+", users_str):
        part = part.strip()
        if part and part.isdigit():
            user_ids.append(int(part))

    openai_base_url = (request.form.get("openai_base_url") or "").strip()
    model_name = (request.form.get("model_name") or "").strip()
    model_fallback_name = (request.form.get("model_fallback_name") or "").strip()
    cloud_fallback = request.form.get("cloud_fallback_enabled") == "1"
    openai_api_key = (request.form.get("openai_api_key") or "").strip()

    set_config_in_redis_sync(redis_url, "TELEGRAM_BOT_TOKEN", token)
    set_config_in_redis_sync(redis_url, "TELEGRAM_ALLOWED_USER_IDS", user_ids if user_ids else "")
    set_config_in_redis_sync(redis_url, "OPENAI_BASE_URL", openai_base_url)
    set_config_in_redis_sync(redis_url, "MODEL_NAME", model_name)
    set_config_in_redis_sync(redis_url, "MODEL_FALLBACK_NAME", model_fallback_name)
    set_config_in_redis_sync(redis_url, "CLOUD_FALLBACK_ENABLED", "true" if cloud_fallback else "false")
    set_config_in_redis_sync(redis_url, "OPENAI_API_KEY", openai_api_key)

    flash("Настройки сохранены. При смене модели перезапустите assistant-core, при смене токена — telegram-adapter.", "success")
    return redirect(url_for("index"))


def main():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
