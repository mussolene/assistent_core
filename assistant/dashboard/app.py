"""Web dashboard for initial setup: Telegram token, allowed users, Redis URL, etc."""

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
    .container { max-width: 520px; margin: 0 auto; }
    h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
    .sub { color: var(--muted); font-size: 0.9rem; margin-bottom: 1.5rem; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; margin-bottom: 1rem; }
    label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 0.35rem; }
    input, textarea { width: 100%; padding: 0.6rem 0.75rem; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 1rem; }
    input:focus, textarea:focus { outline: none; border-color: var(--accent); }
    textarea { min-height: 60px; resize: vertical; }
    .hint { font-size: 0.8rem; color: var(--muted); margin-top: 0.25rem; }
    button { background: var(--accent); color: var(--bg); border: none; padding: 0.65rem 1.25rem; border-radius: 8px; font-size: 1rem; cursor: pointer; font-weight: 600; }
    button:hover { opacity: 0.9; }
    .flash { padding: 0.75rem; border-radius: 8px; margin-bottom: 1rem; }
    .flash.success { background: rgba(34, 197, 94, 0.2); border: 1px solid var(--accent); }
    .flash.error { background: rgba(239, 68, 68, 0.2); border: 1px solid #ef4444; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Настройка ассистента</h1>
    <p class="sub">Задайте токен бота и список пользователей. Токен не запрашивается при запуске контейнеров.</p>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for cat, msg in messages %}
          <div class="flash {{ cat }}">{{ msg }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    <form method="post" action="{{ url_for('save') }}">
      <div class="card">
        <label for="token">Telegram Bot Token</label>
        <input id="token" name="telegram_bot_token" type="password" value="{{ config.get('TELEGRAM_BOT_TOKEN', '') }}" placeholder="123456:ABC..." autocomplete="off">
        <p class="hint">Получить у @BotFather. Сохраняется в Redis.</p>
      </div>
      <div class="card">
        <label for="users">Разрешённые User ID (через запятую)</label>
        <input id="users" name="telegram_allowed_user_ids" type="text" value="{{ config.get('TELEGRAM_ALLOWED_USER_IDS_STR', '') }}" placeholder="123456789, 987654321">
        <p class="hint">Пусто = разрешить всех (только для разработки).</p>
      </div>
      <div class="card">
        <label>Redis URL</label>
        <input type="text" value="{{ redis_url }}" disabled>
        <p class="hint">Задаётся через переменные окружения контейнера.</p>
      </div>
      <button type="submit">Сохранить</button>
    </form>
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
    return render_template_string(
        INDEX_HTML,
        config=config,
        redis_url=get_redis_url(),
    )


@app.route("/save", methods=["POST"])
def save():
    token = (request.form.get("telegram_bot_token") or "").strip()
    users_str = (request.form.get("telegram_allowed_user_ids") or "").strip()
    redis_url = get_redis_url()
    if not token:
        flash("Укажите токен бота.", "error")
        return redirect(url_for("index"))
    user_ids = []
    for part in re.split(r"[\s,]+", users_str):
        part = part.strip()
        if part and part.isdigit():
            user_ids.append(int(part))
    set_config_in_redis_sync(redis_url, "TELEGRAM_BOT_TOKEN", token)
    set_config_in_redis_sync(redis_url, "TELEGRAM_ALLOWED_USER_IDS", user_ids if user_ids else "")
    flash("Настройки сохранены. Перезапустите telegram-adapter: docker compose restart telegram-adapter", "success")
    return redirect(url_for("index"))


def main():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
