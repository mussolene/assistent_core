"""Web dashboard: Telegram, Model, MCP, monitoring. Config in Redis."""

from __future__ import annotations

import asyncio
import json
import os
import re

import httpx
from flask import Flask, request, render_template_string, redirect, url_for, flash, jsonify

from assistant.dashboard.config_store import (
    REDIS_PREFIX,
    MCP_SERVERS_KEY,
    PAIRING_MODE_KEY,
    get_redis_url,
    get_config_from_redis_sync,
    set_config_in_redis_sync,
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-production")

LAYOUT_CSS = """
:root { --bg: #0c0c0f; --card: #16161a; --text: #e4e4e7; --muted: #71717a; --accent: #22c55e; --border: #27272a; --danger: #ef4444; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; min-height: 100vh; }
.nav { display: flex; gap: 0; border-bottom: 1px solid var(--border); padding: 0 1.5rem; background: var(--card); }
.nav a { padding: 1rem 1.25rem; color: var(--muted); text-decoration: none; font-weight: 500; border-bottom: 2px solid transparent; }
.nav a:hover, .nav a.active { color: var(--text); border-bottom-color: var(--accent); }
.container { max-width: 640px; margin: 0 auto; padding: 2rem 1.5rem; }
h1 { font-size: 1.35rem; margin-bottom: 0.25rem; }
.sub { color: var(--muted); font-size: 0.9rem; margin-bottom: 1.5rem; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 0.35rem; }
input[type="text"], input[type="password"], input[type="url"] { width: 100%; padding: 0.6rem 0.75rem; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 1rem; }
input:focus { outline: none; border-color: var(--accent); }
input[type="checkbox"] { width: 1rem; height: 1rem; margin-right: 0.5rem; }
.hint { font-size: 0.8rem; color: var(--muted); margin-top: 0.25rem; }
.row { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; flex-wrap: wrap; }
.btn { background: var(--accent); color: var(--bg); border: none; padding: 0.6rem 1.1rem; border-radius: 8px; font-size: 0.95rem; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; }
.btn:hover { opacity: 0.9; }
.btn-secondary { background: var(--border); color: var(--text); }
.btn-danger { background: var(--danger); color: #fff; }
.flash { padding: 0.75rem; border-radius: 8px; margin-bottom: 1rem; }
.flash.success { background: rgba(34, 197, 94, 0.15); border: 1px solid var(--accent); }
.flash.error { background: rgba(239, 68, 68, 0.15); border: 1px solid var(--danger); }
.monitor-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 1rem; }
.monitor-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 1rem; text-align: center; }
.monitor-card .val { font-size: 1.25rem; font-weight: 600; color: var(--accent); }
.monitor-card .label { font-size: 0.8rem; color: var(--muted); margin-top: 0.25rem; }
.mcp-list { list-style: none; padding: 0; margin: 0; }
.mcp-list li { display: flex; justify-content: space-between; align-items: center; padding: 0.5rem 0; border-bottom: 1px solid var(--border); }
.mcp-list li:last-child { border-bottom: none; }
"""

INDEX_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Assistant — Панель</title>
  <style>{{ layout_css }}</style>
</head>
<body>
  <nav class="nav">
    <a href="{{ url_for('index') }}" class="{{ 'active' if section == 'telegram' else '' }}">Telegram</a>
    <a href="{{ url_for('model') }}" class="{{ 'active' if section == 'model' else '' }}">Модель</a>
    <a href="{{ url_for('mcp') }}" class="{{ 'active' if section == 'mcp' else '' }}">MCP</a>
    <a href="{{ url_for('monitor') }}" class="{{ 'active' if section == 'monitor' else '' }}">Мониторинг</a>
  </nav>
  <div class="container">
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for cat, msg in messages %}
          <div class="flash {{ cat }}">{{ msg }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    {% block content %}{% endblock %}
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
    data.setdefault("PAIRING_MODE", "false")
    data.setdefault(MCP_SERVERS_KEY, [])
    return data


# ----- Telegram body (no extends) -----
_TELEGRAM_BODY = """
<h1>Telegram</h1>
<p class="sub">Токен бота и pairing. Разрешённые ID можно задать вручную или через pairing.</p>
<form method="post" action="/save-telegram">
  <div class="card">
    <label for="token">Bot Token</label>
    <input id="token" name="telegram_bot_token" type="password" value="{{ config.get('TELEGRAM_BOT_TOKEN', '') }}" placeholder="123456:ABC..." autocomplete="off">
    <p class="hint">Получить у @BotFather.</p>
    <button type="button" class="btn btn-secondary" style="margin-top:0.75rem" onclick="testBot()">Проверить бота</button>
    <span id="bot-result" style="margin-left:0.5rem;font-size:0.9rem"></span>
  </div>
  <div class="card">
    <label>Pairing</label>
    <p class="hint">Включите режим pairing, затем отправьте боту в Telegram команду /start — ваш ID будет добавлен в разрешённые.</p>
    <div class="row">
      <input type="checkbox" id="pairing" name="pairing_mode" value="1" {{ 'checked' if config.get('PAIRING_MODE') == 'true' else '' }}>
      <label for="pairing" style="margin-bottom:0">Включить режим pairing</label>
    </div>
  </div>
  <div class="card">
    <label for="users">Разрешённые User ID (через запятую)</label>
    <input id="users" name="telegram_allowed_user_ids" type="text" value="{{ config.get('TELEGRAM_ALLOWED_USER_IDS_STR', '') }}" placeholder="123456789, 987654321">
    <p class="hint">Пусто — разрешить всех (только для разработки).</p>
  </div>
  <button type="submit" class="btn">Сохранить</button>
</form>
<script>
function testBot() {
  var r = document.getElementById('bot-result');
  r.textContent = '…';
  fetch('/api/test-bot', { method: 'POST' })
    .then(function(res) { return res.json(); })
    .then(function(d) { r.textContent = d.ok ? 'OK: ' + (d.username || '') : 'Ошибка: ' + (d.error || 'unknown'); })
    .catch(function(e) { r.textContent = 'Ошибка: ' + e.message; });
}
</script>
"""


@app.route("/")
def index():
    config = load_config()
    return render_template_string(
        INDEX_HTML.replace("{{ layout_css }}", LAYOUT_CSS).replace("{% block content %}{% endblock %}", _TELEGRAM_BODY),
        config=config,
        section="telegram",
    )


@app.route("/save-telegram", methods=["POST"])
def save_telegram():
    redis_url = get_redis_url()
    token = (request.form.get("telegram_bot_token") or "").strip()
    if not token:
        flash("Укажите токен бота.", "error")
        return redirect(url_for("index"))
    users_str = (request.form.get("telegram_allowed_user_ids") or "").strip()
    user_ids = [int(x.strip()) for x in re.split(r"[\s,]+", users_str) if x.strip() and x.strip().isdigit()]
    pairing = request.form.get("pairing_mode") == "1"
    set_config_in_redis_sync(redis_url, "TELEGRAM_BOT_TOKEN", token)
    set_config_in_redis_sync(redis_url, "TELEGRAM_ALLOWED_USER_IDS", user_ids if user_ids else [])
    set_config_in_redis_sync(redis_url, PAIRING_MODE_KEY, "true" if pairing else "false")
    flash("Сохранено. При смене токена перезапустите telegram-adapter.", "success")
    return redirect(url_for("index"))


# ----- Model -----
_MODEL_BODY = """
<h1>Модель</h1>
<p class="sub">Подключение к API (Ollama / OpenAI-совместимый).</p>
<form method="post" action="/save-model">
  <div class="card">
    <label for="base_url">URL API</label>
    <input id="base_url" name="openai_base_url" type="url" value="{{ config.get('OPENAI_BASE_URL', '') }}" placeholder="http://host.docker.internal:11434/v1">
    <p class="hint">Для Docker: host.docker.internal:11434. Локально: localhost:11434.</p>
  </div>
  <div class="card">
    <label for="model_name">Имя модели</label>
    <input id="model_name" name="model_name" type="text" value="{{ config.get('MODEL_NAME', '') }}" placeholder="llama3.2">
  </div>
  <div class="card">
    <label for="fallback_name">Fallback (облако)</label>
    <input id="fallback_name" name="model_fallback_name" type="text" value="{{ config.get('MODEL_FALLBACK_NAME', '') }}" placeholder="gpt-4">
  </div>
  <div class="card">
    <div class="row">
      <input type="checkbox" id="cloud" name="cloud_fallback_enabled" value="1" {{ 'checked' if config.get('CLOUD_FALLBACK_ENABLED') == 'true' else '' }}>
      <label for="cloud" style="margin-bottom:0">Облачный fallback</label>
    </div>
  </div>
  <div class="card">
    <div class="row">
      <input type="checkbox" id="lm_native" name="lm_studio_native" value="1" {{ 'checked' if config.get('LM_STUDIO_NATIVE') == 'true' else '' }}>
      <label for="lm_native" style="margin-bottom:0">LM Studio native API</label>
    </div>
    <p class="hint">Стриминг по <a href="https://lmstudio.ai/docs/developer/rest/streaming-events" target="_blank" rel="noopener">SSE</a>: размышления (reasoning) скрыты, в чат дописывается только итоговый ответ.</p>
  </div>
  <div class="card">
    <label for="api_key">API ключ</label>
    <input id="api_key" name="openai_api_key" type="password" value="{{ config.get('OPENAI_API_KEY', '') }}" placeholder="sk-... или ollama" autocomplete="off">
  </div>
  <button type="submit" class="btn">Сохранить</button>
  <button type="button" class="btn btn-secondary" style="margin-left:0.5rem" onclick="testModel()">Проверить подключение</button>
  <span id="model-result" style="margin-left:0.5rem;font-size:0.9rem"></span>
</form>
<script>
function testModel() {
  var r = document.getElementById('model-result');
  r.textContent = '…';
  fetch('/api/test-model', { method: 'POST' })
    .then(function(res) { return res.json(); })
    .then(function(d) { r.textContent = d.ok ? 'OK' : 'Ошибка: ' + (d.error || 'unknown'); })
    .catch(function(e) { r.textContent = 'Ошибка: ' + e.message; });
}
</script>
"""


@app.route("/model")
def model():
    config = load_config()
    return render_template_string(
        INDEX_HTML.replace("{{ layout_css }}", LAYOUT_CSS).replace("{% block content %}{% endblock %}", _MODEL_BODY),
        config=config,
        section="model",
    )


@app.route("/save-model", methods=["POST"])
def save_model():
    redis_url = get_redis_url()
    set_config_in_redis_sync(redis_url, "OPENAI_BASE_URL", (request.form.get("openai_base_url") or "").strip())
    set_config_in_redis_sync(redis_url, "MODEL_NAME", (request.form.get("model_name") or "").strip())
    set_config_in_redis_sync(redis_url, "MODEL_FALLBACK_NAME", (request.form.get("model_fallback_name") or "").strip())
    set_config_in_redis_sync(redis_url, "CLOUD_FALLBACK_ENABLED", "true" if request.form.get("cloud_fallback_enabled") == "1" else "false")
    set_config_in_redis_sync(redis_url, "LM_STUDIO_NATIVE", "true" if request.form.get("lm_studio_native") == "1" else "false")
    set_config_in_redis_sync(redis_url, "OPENAI_API_KEY", (request.form.get("openai_api_key") or "").strip())
    flash("Сохранено. Перезапустите assistant-core при смене модели.", "success")
    return redirect(url_for("model"))


# ----- MCP -----
_MCP_BODY = """
<h1>MCP скиллы</h1>
<p class="sub">Подключение MCP-серверов: имя, URL и опциональные аргументы (JSON). Конфигурация в Redis.</p>
<form method="post" action="/save-mcp" id="mcp-form">
  <div class="card">
    <label for="mcp_name">Имя</label>
    <input id="mcp_name" name="mcp_name" type="text" placeholder="my-mcp">
    <label for="mcp_url" style="margin-top:0.75rem">URL</label>
    <input id="mcp_url" name="mcp_url" type="url" placeholder="http://localhost:3000">
    <label for="mcp_args" style="margin-top:0.75rem">Аргументы (JSON, опционально)</label>
    <input id="mcp_args" name="mcp_args" type="text" placeholder='{"api_key": "..."}' style="font-family:monospace">
    <p class="hint">Например: {"api_key": "xxx"} или {"transport": "stdio", "command": "npx", "args": ["-y", "mcp-server"]}</p>
    <button type="submit" class="btn" style="margin-top:0.75rem">Добавить</button>
  </div>
</form>
<ul class="mcp-list">
  {% for s in config.get('MCP_SERVERS', []) %}
  <li>
    <span>{{ s.get('name', '') }} — {{ s.get('url', '') }}{% if s.get('args') %} <small style="color:var(--muted)">(args)</small>{% endif %}</span>
    <form method="post" action="/remove-mcp" style="display:inline" onsubmit="return confirm('Удалить?');">
      <input type="hidden" name="index" value="{{ loop.index0 }}">
      <button type="submit" class="btn btn-danger" style="padding:0.35rem 0.6rem;font-size:0.85rem">Удалить</button>
    </form>
  </li>
  {% endfor %}
</ul>
{% if not config.get('MCP_SERVERS') %}<p class="hint">Список пуст. Добавьте MCP-сервер выше.</p>{% endif %}
"""


@app.route("/mcp")
def mcp():
    config = load_config()
    return render_template_string(
        INDEX_HTML.replace("{{ layout_css }}", LAYOUT_CSS).replace("{% block content %}{% endblock %}", _MCP_BODY),
        config=config,
        section="mcp",
    )


@app.route("/save-mcp", methods=["POST"])
def save_mcp():
    redis_url = get_redis_url()
    config = load_config()
    servers = list(config.get(MCP_SERVERS_KEY) or [])
    name = (request.form.get("mcp_name") or "").strip()
    url = (request.form.get("mcp_url") or "").strip()
    args_raw = (request.form.get("mcp_args") or "").strip()
    args = None
    if args_raw:
        try:
            args = json.loads(args_raw)
            if not isinstance(args, dict):
                args = None
        except json.JSONDecodeError:
            flash("Аргументы MCP: неверный JSON.", "error")
            return redirect(url_for("mcp"))
    if name and url:
        entry = {"name": name, "url": url}
        if args is not None:
            entry["args"] = args
        servers.append(entry)
        set_config_in_redis_sync(redis_url, MCP_SERVERS_KEY, servers)
        flash("MCP-сервер добавлен.", "success")
    return redirect(url_for("mcp"))


@app.route("/remove-mcp", methods=["POST"])
def remove_mcp():
    redis_url = get_redis_url()
    config = load_config()
    servers = list(config.get(MCP_SERVERS_KEY) or [])
    try:
        idx = int(request.form.get("index", -1))
        if 0 <= idx < len(servers):
            servers.pop(idx)
            set_config_in_redis_sync(redis_url, MCP_SERVERS_KEY, servers)
            flash("MCP-сервер удалён.", "success")
    except ValueError:
        pass
    return redirect(url_for("mcp"))


# ----- Monitor -----
_MONITOR_BODY = """
<h1>Мониторинг</h1>
<p class="sub">Ресурсы Redis (память и подключения).</p>
<div class="monitor-grid">
  <div class="monitor-card"><div class="val" id="mem">{{ info.get('used_memory_human', '—') }}</div><div class="label">Память Redis</div></div>
  <div class="monitor-card"><div class="val" id="clients">{{ info.get('connected_clients', '—') }}</div><div class="label">Подключения</div></div>
  <div class="monitor-card"><div class="val" id="keys">{{ info.get('keys', '—') }}</div><div class="label">Ключей (assistant:*)</div></div>
</div>
<p class="hint" style="margin-top:1rem">Обновление при загрузке страницы. Перезагрузите для актуальных данных.</p>
"""


@app.route("/monitor")
def monitor():
    config = load_config()
    info = _redis_info()
    return render_template_string(
        INDEX_HTML.replace("{{ layout_css }}", LAYOUT_CSS).replace("{% block content %}{% endblock %}", _MONITOR_BODY),
        config=config,
        section="monitor",
        info=info,
    )


def _redis_info() -> dict:
    try:
        import redis
        client = redis.from_url(get_redis_url(), decode_responses=True)
        raw = client.info("memory")
        raw["connected_clients"] = client.info("clients").get("connected_clients", 0)
        keys = len(client.keys(REDIS_PREFIX + "*"))
        raw["keys"] = keys
        client.close()
        return raw
    except Exception:
        return {}


# ----- API -----
def _model_check_hint(err_text: str) -> str:
    if not err_text:
        return ""
    err_lower = err_text.lower()
    if "connection" in err_lower or "refused" in err_lower or "cannot connect" in err_lower:
        return err_text + " Подсказка: из Docker используйте host.docker.internal вместо localhost."
    return err_text


@app.route("/api/test-model", methods=["POST"])
def api_test_model():
    redis_url = get_redis_url()
    cfg = get_config_from_redis_sync(redis_url)
    base_url = (cfg.get("OPENAI_BASE_URL") or "").strip() or "http://localhost:11434/v1"
    model_name = (cfg.get("MODEL_NAME") or "").strip() or "llama3.2"
    api_key = (cfg.get("OPENAI_API_KEY") or "").strip() or "ollama"

    async def _check():
        from openai import AsyncOpenAI
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0))
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=http_client,
        )
        try:
            r = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            return bool(r.choices)
        except Exception as e:
            return _model_check_hint(str(e))

    try:
        err = asyncio.run(_check())
        if err is True:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": err if isinstance(err, str) else "No response"})
    except Exception as e:
        return jsonify({"ok": False, "error": _model_check_hint(str(e))})


@app.route("/api/test-bot", methods=["POST"])
def api_test_bot():
    redis_url = get_redis_url()
    cfg = get_config_from_redis_sync(redis_url)
    token = (cfg.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return jsonify({"ok": False, "error": "Token not set"})
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10.0)
        data = r.json()
        if data.get("ok"):
            return jsonify({"ok": True, "username": data.get("result", {}).get("username", "")})
        return jsonify({"ok": False, "error": data.get("description", "unknown")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/monitor")
def api_monitor():
    return jsonify(_redis_info())


def main():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
