"""Web dashboard: Telegram, Model, MCP, monitoring. Config in Redis. Auth: users/sessions in Redis."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template_string,
    request,
    session,
    stream_with_context,
    url_for,
)

from assistant.dashboard.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL,
    create_session,
    create_user,
    delete_session,
    get_current_user,
    get_redis,
    setup_done,
    verify_user,
)
from assistant.dashboard.config_store import (
    MCP_SERVERS_KEY,
    PAIRING_MODE_KEY,
    REDIS_PREFIX,
    create_pairing_code,
    get_config_from_redis_sync,
    get_redis_url,
    set_config_in_redis_sync,
)

app = Flask(__name__)
_secret_key = os.getenv("SECRET_KEY", "change-me-in-production")
app.secret_key = _secret_key
if _secret_key == "change-me-in-production":
    import logging

    logging.getLogger(__name__).warning(
        "SECRET_KEY not set; using default. Set SECRET_KEY in production."
    )

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
  <link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
  <style>{{ layout_css }}</style>
</head>
<body>
  <nav class="nav">
    <a href="{{ url_for('index') }}" class="{{ 'active' if section == 'telegram' else '' }}">Telegram</a>
    <a href="{{ url_for('model') }}" class="{{ 'active' if section == 'model' else '' }}">Модель</a>
    <a href="{{ url_for('mcp') }}" class="{{ 'active' if section == 'mcp' else '' }}">MCP</a>
    <a href="{{ url_for('monitor') }}" class="{{ 'active' if section == 'monitor' else '' }}">Мониторинг</a>
    <a href="{{ url_for('repos_page') }}" class="{{ 'active' if section == 'repos' else '' }}">Репо</a>
    <a href="{{ url_for('email_settings') }}" class="{{ 'active' if section == 'email' else '' }}">Email</a>
    <a href="{{ url_for('mcp_agent') }}" class="{{ 'active' if section == 'mcp_agent' else '' }}">MCP (агент)</a>
    {% if current_user %}
    <span style="margin-left:auto;color:var(--muted);font-size:0.9rem">{{ current_user.display_name or current_user.login }} ({{ current_user.role }})</span>
    <a href="{{ url_for('logout') }}" style="margin-left:0.5rem">Выйти</a>
    {% endif %}
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

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Вход — Assistant</title>
  <link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
  <style>{{ layout_css }}</style>
</head>
<body>
  <div class="container" style="max-width:360px;padding-top:4rem">
    <h1>Вход</h1>
    <p class="sub">Введите логин и пароль.</p>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for cat, msg in messages %}
          <div class="flash {{ cat }}">{{ msg }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    <form method="post" action="{{ url_for('login') }}">
      <input type="hidden" name="next" value="{{ next or '' }}">
      <div class="card">
        <label for="login">Логин</label>
        <input id="login" name="login" type="text" required autocomplete="username">
        <label for="password" style="margin-top:0.75rem">Пароль</label>
        <input id="password" name="password" type="password" required autocomplete="current-password">
      </div>
      <button type="submit" class="btn">Войти</button>
    </form>
  </div>
</body>
</html>
"""

SETUP_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Первичная настройка — Assistant</title>
  <link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
  <style>{{ layout_css }}</style>
</head>
<body>
  <div class="container" style="max-width:400px;padding-top:3rem">
    <h1>Первичная настройка</h1>
    <p class="sub">Создайте учётную запись владельца (owner).</p>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for cat, msg in messages %}
          <div class="flash {{ cat }}">{{ msg }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    <form method="post" action="{{ url_for('setup') }}">
      <div class="card">
        <label for="login">Логин</label>
        <input id="login" name="login" type="text" required autocomplete="username" minlength="2">
        <label for="password" style="margin-top:0.75rem">Пароль</label>
        <input id="password" name="password" type="password" required autocomplete="new-password" minlength="6">
        <label for="password2" style="margin-top:0.75rem">Подтверждение пароля</label>
        <input id="password2" name="password2" type="password" required autocomplete="new-password">
      </div>
      <button type="submit" class="btn">Создать и войти</button>
    </form>
  </div>
</body>
</html>
"""


def load_config() -> dict:
    redis_url = get_redis_url()
    data = get_config_from_redis_sync(redis_url)
    if "TELEGRAM_ALLOWED_USER_IDS" in data and isinstance(data["TELEGRAM_ALLOWED_USER_IDS"], list):
        data["TELEGRAM_ALLOWED_USER_IDS_STR"] = ",".join(
            str(x) for x in data["TELEGRAM_ALLOWED_USER_IDS"]
        )
    else:
        data["TELEGRAM_ALLOWED_USER_IDS_STR"] = data.get("TELEGRAM_ALLOWED_USER_IDS", "") or ""
    data.setdefault("PAIRING_MODE", "false")
    data.setdefault(MCP_SERVERS_KEY, [])
    return data


@app.context_processor
def _inject_current_user():
    try:
        user = get_current_user(get_redis())
        return {"current_user": user}
    except Exception:
        return {"current_user": None}


@app.before_request
def _require_auth():
    """Redirect to setup or login when needed."""
    path = request.path
    if path in ("/login", "/logout", "/api/session"):
        return None
    if path.startswith("/mcp/v1/"):
        return None
    if path == "/setup" and request.method in ("GET", "POST"):
        return None
    try:
        r = get_redis()
    except Exception:
        return None
    if not setup_done(r):
        if path.startswith("/setup"):
            return None
        return redirect(url_for("setup"))
    user = get_current_user(r)
    if user:
        return None
    if path.startswith("/setup"):
        return None
    return redirect(url_for("login", next=request.url))


# ----- Auth routes -----
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template_string(
            LOGIN_HTML.replace("{{ layout_css }}", LAYOUT_CSS),
            next=request.args.get("next"),
        )
    login_name = (request.form.get("login") or "").strip()
    password = request.form.get("password") or ""
    next_url = request.form.get("next") or url_for("index")
    if not login_name or not password:
        flash("Укажите логин и пароль.", "error")
        return redirect(url_for("login", next=next_url))
    r = get_redis()
    user = verify_user(r, login_name, password)
    if not user:
        try:
            from assistant.security.audit import audit

            audit("login_failed")
        except Exception:
            pass
        flash("Неверный логин или пароль.", "error")
        return redirect(url_for("login", next=next_url))
    sid = create_session(r, login_name)
    resp = make_response(redirect(next_url))
    _set_session_cookie(resp, sid)
    try:
        from assistant.security.audit import audit

        audit("login_ok", login=login_name)
    except Exception:
        pass
    return resp


def _set_session_cookie(resp, sid: str) -> None:
    """Set session cookie; use secure=True when HTTPS or production."""
    secure = (
        os.getenv("HTTPS", "").lower() in ("1", "true", "yes")
        or os.getenv("FLASK_ENV") == "production"
    )
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        sid,
        max_age=SESSION_TTL,
        httponly=True,
        samesite="Lax",
        secure=secure,
    )


@app.route("/api/session", methods=["GET"])
def api_session():
    """JSON: текущая сессия для фронта. Без редиректа."""
    r = get_redis()
    user = get_current_user(r)
    if user:
        return jsonify(
            {
                "logged_in": True,
                "login": user.get("login"),
                "role": user.get("role"),
                "display_name": user.get("display_name"),
            }
        )
    return jsonify({"logged_in": False})


@app.route("/logout")
def logout():
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if sid:
        try:
            delete_session(get_redis(), sid)
            from assistant.security.audit import audit

            audit("logout")
        except Exception:
            pass
    resp = make_response(redirect(url_for("login")))
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp


@app.route("/setup", methods=["GET", "POST"])
def setup():
    try:
        r = get_redis()
    except Exception as e:
        flash(f"Ошибка подключения к Redis: {e}", "error")
        return render_template_string(SETUP_HTML.replace("{{ layout_css }}", LAYOUT_CSS))
    if setup_done(r):
        return redirect(url_for("index"))
    if request.method == "GET":
        return render_template_string(SETUP_HTML.replace("{{ layout_css }}", LAYOUT_CSS))
    login_name = (request.form.get("login") or "").strip()
    password = request.form.get("password") or ""
    password2 = request.form.get("password2") or ""
    if not login_name or len(login_name) < 2:
        flash("Логин не менее 2 символов.", "error")
        return redirect(url_for("setup"))
    if len(password) < 6:
        flash("Пароль не менее 6 символов.", "error")
        return redirect(url_for("setup"))
    if password != password2:
        flash("Пароли не совпадают.", "error")
        return redirect(url_for("setup"))
    try:
        create_user(r, login_name, password, role="owner")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("setup"))
    sid = create_session(r, login_name)
    resp = make_response(redirect(url_for("index")))
    _set_session_cookie(resp, sid)
    try:
        from assistant.security.audit import audit

        audit("setup_completed", login=login_name)
    except Exception:
        pass
    return resp


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
  <div class="card">
    <label for="dev_chat_id">Chat ID для MCP/агента (уведомления, confirm)</label>
    <input id="dev_chat_id" name="telegram_dev_chat_id" type="text" value="{{ config.get('TELEGRAM_DEV_CHAT_ID', '') }}" placeholder="123456789">
    <p class="hint">Куда слать сообщения от MCP-сервера (notify, ask_confirmation). Для личных чатов = User ID. Пусто — первый из разрешённых.</p>
  </div>
  <div class="card">
    <label>Быстрая привязка</label>
    <p class="hint">Сгенерируйте код. Отправьте боту в Telegram: /start КОД или /pair КОД — пользователь будет добавлен в разрешённые без глобального режима pairing.</p>
    <button type="button" class="btn btn-secondary" onclick="genPairingCode()">Сгенерировать код</button>
    <span id="pairing-result" style="margin-left:0.5rem;font-size:0.9rem"></span>
    <div id="pairing-code-block" style="margin-top:0.75rem;display:none">
      <p class="hint">Код: <strong id="pairing-code"></strong> (действует <span id="pairing-expires"></span> с)</p>
      <p class="hint">Ссылка: <a id="pairing-link" href="#" target="_blank" rel="noopener"></a></p>
    </div>
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
function genPairingCode() {
  var block = document.getElementById('pairing-code-block');
  var result = document.getElementById('pairing-result');
  result.textContent = '…';
  block.style.display = 'none';
  fetch('/api/pairing-code', { method: 'POST' })
    .then(function(res) { return res.json(); })
    .then(function(d) {
      if (d.error) { result.textContent = 'Ошибка: ' + d.error; return; }
      document.getElementById('pairing-code').textContent = d.code;
      document.getElementById('pairing-expires').textContent = d.expires_in_sec || 600;
      var linkEl = document.getElementById('pairing-link');
      if (d.link) { linkEl.href = d.link; linkEl.textContent = d.link; linkEl.style.display = ''; }
      else { linkEl.style.display = 'none'; }
      block.style.display = 'block';
      result.textContent = 'Готово';
    })
    .catch(function(e) { result.textContent = 'Ошибка: ' + e.message; });
}
</script>
"""


@app.route("/")
def index():
    config = load_config()
    return render_template_string(
        INDEX_HTML.replace("{{ layout_css }}", LAYOUT_CSS).replace(
            "{% block content %}{% endblock %}", _TELEGRAM_BODY
        ),
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
    user_ids = [
        int(x.strip()) for x in re.split(r"[\s,]+", users_str) if x.strip() and x.strip().isdigit()
    ]
    pairing = request.form.get("pairing_mode") == "1"
    set_config_in_redis_sync(redis_url, "TELEGRAM_BOT_TOKEN", token)
    set_config_in_redis_sync(redis_url, "TELEGRAM_ALLOWED_USER_IDS", user_ids if user_ids else [])
    set_config_in_redis_sync(redis_url, PAIRING_MODE_KEY, "true" if pairing else "false")
    set_config_in_redis_sync(
        redis_url, "TELEGRAM_DEV_CHAT_ID", (request.form.get("telegram_dev_chat_id") or "").strip()
    )
    flash("Сохранено. Настройки применяются автоматически.", "success")
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
        INDEX_HTML.replace("{{ layout_css }}", LAYOUT_CSS).replace(
            "{% block content %}{% endblock %}", _MODEL_BODY
        ),
        config=config,
        section="model",
    )


@app.route("/save-model", methods=["POST"])
def save_model():
    redis_url = get_redis_url()
    set_config_in_redis_sync(
        redis_url, "OPENAI_BASE_URL", (request.form.get("openai_base_url") or "").strip()
    )
    set_config_in_redis_sync(
        redis_url, "MODEL_NAME", (request.form.get("model_name") or "").strip()
    )
    set_config_in_redis_sync(
        redis_url, "MODEL_FALLBACK_NAME", (request.form.get("model_fallback_name") or "").strip()
    )
    set_config_in_redis_sync(
        redis_url,
        "CLOUD_FALLBACK_ENABLED",
        "true" if request.form.get("cloud_fallback_enabled") == "1" else "false",
    )
    set_config_in_redis_sync(
        redis_url,
        "LM_STUDIO_NATIVE",
        "true" if request.form.get("lm_studio_native") == "1" else "false",
    )
    set_config_in_redis_sync(
        redis_url, "OPENAI_API_KEY", (request.form.get("openai_api_key") or "").strip()
    )
    flash("Сохранено. Настройки модели применяются автоматически.", "success")
    return redirect(url_for("model"))


# ----- Email -----
_EMAIL_BODY = """
<h1>Email</h1>
<p class="sub">Настройки канала отправки писем (для скилла send_email и ответов по каналу Email).</p>
<form method="post" action="/save-email">
  <div class="card">
    <div class="row">
      <input type="checkbox" id="email_enabled" name="email_enabled" value="1" {{ 'checked' if config.get('EMAIL_ENABLED') == 'true' else '' }}>
      <label for="email_enabled" style="margin-bottom:0">Включить отправку писем</label>
    </div>
  </div>
  <div class="card">
    <label for="email_from">Адрес отправителя (From)</label>
    <input id="email_from" name="email_from" type="text" value="{{ config.get('EMAIL_FROM', '') }}" placeholder="bot@example.com">
  </div>
  <div class="card">
    <label for="email_provider">Провайдер</label>
    <input id="email_provider" name="email_provider" type="text" value="{{ config.get('EMAIL_PROVIDER', 'smtp') }}" placeholder="smtp или sendgrid">
    <p class="hint">smtp — свой SMTP; sendgrid — API SendGrid.</p>
  </div>
  <div class="card">
    <label for="email_smtp_host">SMTP: хост</label>
    <input id="email_smtp_host" name="email_smtp_host" type="text" value="{{ config.get('EMAIL_SMTP_HOST', '') }}" placeholder="smtp.gmail.com">
  </div>
  <div class="card">
    <label for="email_smtp_port">SMTP: порт</label>
    <input id="email_smtp_port" name="email_smtp_port" type="text" value="{{ config.get('EMAIL_SMTP_PORT', '587') }}" placeholder="587">
  </div>
  <div class="card">
    <label for="email_smtp_user">SMTP: пользователь</label>
    <input id="email_smtp_user" name="email_smtp_user" type="text" value="{{ config.get('EMAIL_SMTP_USER', '') }}">
  </div>
  <div class="card">
    <label for="email_smtp_password">SMTP: пароль</label>
    <input id="email_smtp_password" name="email_smtp_password" type="password" value="{{ config.get('EMAIL_SMTP_PASSWORD', '') }}" autocomplete="off">
  </div>
  <div class="card">
    <label for="email_sendgrid_key">SendGrid API Key (если провайдер sendgrid)</label>
    <input id="email_sendgrid_key" name="email_sendgrid_key" type="password" value="{{ config.get('EMAIL_SENDGRID_API_KEY', '') }}" autocomplete="off">
  </div>
  <button type="submit" class="btn">Сохранить</button>
</form>
"""


@app.route("/email")
def email_settings():
    config = load_config()
    return render_template_string(
        INDEX_HTML.replace("{{ layout_css }}", LAYOUT_CSS).replace(
            "{% block content %}{% endblock %}", _EMAIL_BODY
        ),
        config=config,
        section="email",
    )


@app.route("/save-email", methods=["POST"])
def save_email():
    redis_url = get_redis_url()
    set_config_in_redis_sync(
        redis_url, "EMAIL_ENABLED", "true" if request.form.get("email_enabled") == "1" else "false"
    )
    set_config_in_redis_sync(
        redis_url, "EMAIL_FROM", (request.form.get("email_from") or "").strip()
    )
    set_config_in_redis_sync(
        redis_url, "EMAIL_PROVIDER", (request.form.get("email_provider") or "smtp").strip().lower()
    )
    set_config_in_redis_sync(
        redis_url, "EMAIL_SMTP_HOST", (request.form.get("email_smtp_host") or "").strip()
    )
    set_config_in_redis_sync(
        redis_url, "EMAIL_SMTP_PORT", (request.form.get("email_smtp_port") or "587").strip()
    )
    set_config_in_redis_sync(
        redis_url, "EMAIL_SMTP_USER", (request.form.get("email_smtp_user") or "").strip()
    )
    set_config_in_redis_sync(
        redis_url, "EMAIL_SMTP_PASSWORD", (request.form.get("email_smtp_password") or "").strip()
    )
    set_config_in_redis_sync(
        redis_url, "EMAIL_SENDGRID_API_KEY", (request.form.get("email_sendgrid_key") or "").strip()
    )
    flash("Настройки Email сохранены.", "success")
    return redirect(url_for("email_settings"))


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
        INDEX_HTML.replace("{{ layout_css }}", LAYOUT_CSS).replace(
            "{% block content %}{% endblock %}", _MCP_BODY
        ),
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


# ----- MCP Agent (URL + secret для Cursor) -----
_MCP_AGENT_BODY = """
<h1>MCP (агент)</h1>
<p class="sub">Endpoint'ы для доступа агента (Cursor и др.) по HTTP/SSE: URL и секрет для подстановки в MCP config.</p>
{% if new_secret %}
<div class="card flash success">
  <strong>Новый endpoint создан.</strong> Скопируйте данные — секрет больше не отображается.
  <p style="margin-top:0.75rem"><label>URL</label><br>
  <input type="text" id="new-url" value="{{ new_secret.url }}" readonly style="width:100%%;font-family:monospace"></p>
  <p><label>Секрет (Authorization: Bearer)</label><br>
  <input type="text" id="new-secret" value="{{ new_secret.secret }}" readonly style="width:100%%;font-family:monospace"></p>
  <button type="button" class="btn btn-secondary" onclick="navigator.clipboard.writeText(document.getElementById('new-secret').value); this.textContent='Скопировано'">Скопировать секрет</button>
</div>
{% endif %}
<form method="post" action="/mcp-agent/create">
  <div class="card">
    <label for="mcp_agent_name">Имя (например: Cursor)</label>
    <input id="mcp_agent_name" name="name" type="text" placeholder="Cursor" required>
    <label for="mcp_agent_chat_id" style="margin-top:0.75rem">Telegram Chat ID (личный чат = User ID)</label>
    <input id="mcp_agent_chat_id" name="chat_id" type="text" placeholder="123456789" required>
    <p class="hint">Куда слать уведомления и запросы подтверждения.</p>
    <button type="submit" class="btn" style="margin-top:0.75rem">Создать endpoint</button>
  </div>
</form>
<ul class="mcp-list">
  {% for ep in mcp_endpoints %}
  <li>
    <div>
      <strong>{{ ep.name }}</strong> — chat {{ ep.chat_id }}<br>
      <small style="color:var(--muted)">URL: {{ base_url }}mcp/v1/agent/{{ ep.id }}</small>
    </div>
    <form method="post" action="/mcp-agent/regenerate" style="display:inline">
      <input type="hidden" name="endpoint_id" value="{{ ep.id }}">
      <button type="submit" class="btn btn-secondary" style="padding:0.35rem 0.6rem;font-size:0.85rem">Новый секрет</button>
    </form>
    <form method="post" action="/mcp-agent/delete" style="display:inline" onsubmit="return confirm('Удалить endpoint?');">
      <input type="hidden" name="endpoint_id" value="{{ ep.id }}">
      <button type="submit" class="btn btn-danger" style="padding:0.35rem 0.6rem;font-size:0.85rem">Удалить</button>
    </form>
  </li>
  {% endfor %}
</ul>
{% if not mcp_endpoints and not new_secret %}<p class="hint">Создайте endpoint: укажите имя и Telegram Chat ID. В ответ получите URL и секрет для MCP config.</p>{% endif %}
<p class="hint" style="margin-top:1rem">API: POST /mcp/v1/agent/&lt;id&gt;/notify, /question, /confirmation; GET /replies, /events (SSE). Заголовок: Authorization: Bearer &lt;секрет&gt;.</p>
"""


def _mcp_agent_base_url():
    return request.host_url.rstrip("/") + "/"


@app.route("/mcp-agent")
def mcp_agent():
    from assistant.dashboard.mcp_endpoints import list_endpoints

    config = load_config()
    new_secret = None
    if "mcp_new_secret" in session:
        new_secret = session.pop("mcp_new_secret", None)
    base_url = _mcp_agent_base_url()
    return render_template_string(
        INDEX_HTML.replace("{{ layout_css }}", LAYOUT_CSS).replace(
            "{% block content %}{% endblock %}", _MCP_AGENT_BODY
        ),
        config=config,
        section="mcp_agent",
        mcp_endpoints=list_endpoints(),
        new_secret=new_secret,
        base_url=base_url,
    )


@app.route("/mcp-agent/create", methods=["POST"])
def mcp_agent_create():
    from assistant.dashboard.mcp_endpoints import create_endpoint

    name = (request.form.get("name") or "").strip()
    chat_id = (request.form.get("chat_id") or "").strip()
    if not name or not chat_id:
        flash("Укажите имя и Chat ID.", "error")
        return redirect(url_for("mcp_agent"))
    endpoint_id, secret = create_endpoint(name, chat_id)
    base_url = _mcp_agent_base_url()
    session["mcp_new_secret"] = {"url": base_url + "mcp/v1/agent/" + endpoint_id, "secret": secret}
    flash("Endpoint создан. Скопируйте URL и секрет ниже.", "success")
    return redirect(url_for("mcp_agent"))


@app.route("/mcp-agent/regenerate", methods=["POST"])
def mcp_agent_regenerate():
    from assistant.dashboard.mcp_endpoints import regenerate_endpoint_secret

    endpoint_id = (request.form.get("endpoint_id") or "").strip()
    if not endpoint_id:
        return redirect(url_for("mcp_agent"))
    new_secret = regenerate_endpoint_secret(endpoint_id)
    if new_secret:
        session["mcp_new_secret"] = {
            "url": _mcp_agent_base_url() + "mcp/v1/agent/" + endpoint_id,
            "secret": new_secret,
        }
        flash("Секрет обновлён. Скопируйте новый секрет ниже.", "success")
    return redirect(url_for("mcp_agent"))


@app.route("/mcp-agent/delete", methods=["POST"])
def mcp_agent_delete():
    from assistant.dashboard.mcp_endpoints import delete_endpoint

    endpoint_id = (request.form.get("endpoint_id") or "").strip()
    if endpoint_id:
        delete_endpoint(endpoint_id)
        flash("Endpoint удалён.", "success")
    return redirect(url_for("mcp_agent"))


def _mcp_api_auth(endpoint_id: str):
    """Проверка Bearer для MCP API. Возвращает chat_id или None."""
    from assistant.dashboard.mcp_endpoints import get_chat_id_for_endpoint, verify_endpoint_secret

    if not endpoint_id:
        return None
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        secret = auth[7:].strip()
        if verify_endpoint_secret(endpoint_id, secret):
            return get_chat_id_for_endpoint(endpoint_id)
    return None


@app.route("/mcp/v1/agent/<endpoint_id>", methods=["GET"])
def mcp_api_base_get(endpoint_id):
    """GET базового URL: описание API (Cursor и др. могут запрашивать без суффикса)."""
    chat_id = _mcp_api_auth(endpoint_id)
    if not chat_id:
        return jsonify({"error": "Unauthorized"}), 401
    base = _mcp_agent_base_url()
    return jsonify(
        {
            "protocol": "mcp",
            "endpoint_id": endpoint_id,
            "links": {
                "notify": base + f"mcp/v1/agent/{endpoint_id}/notify",
                "question": base + f"mcp/v1/agent/{endpoint_id}/question",
                "confirmation": base + f"mcp/v1/agent/{endpoint_id}/confirmation",
                "replies": base + f"mcp/v1/agent/{endpoint_id}/replies",
                "events": base + f"mcp/v1/agent/{endpoint_id}/events",
            },
            "auth": "Authorization: Bearer <secret>",
        }
    )


def _mcp_tools_call(chat_id: str, endpoint_id: str, name: str, arguments: dict) -> dict:
    """Обработка tools/call для endpoint (chat_id из auth)."""
    import time

    from assistant.core.notify import (
        get_and_clear_pending_result,
        notify_to_chat,
        pop_dev_feedback,
        send_confirmation_request,
    )

    if name == "notify":
        msg = (arguments.get("message") or "").strip()
        if not msg:
            return {"content": [{"type": "text", "text": "Ошибка: message пустой."}]}
        ok = notify_to_chat(chat_id, msg)
        return {
            "content": [{"type": "text", "text": "Отправлено." if ok else "Не удалось отправить."}]
        }

    if name == "ask_confirmation":
        msg = (arguments.get("message") or "").strip()
        timeout_sec = int(arguments.get("timeout_sec") or 120)
        if not msg:
            return {"content": [{"type": "text", "text": "Ошибка: message пустой."}]}
        send_confirmation_request(chat_id, msg)
        deadline = time.monotonic() + min(timeout_sec, 600)
        while time.monotonic() < deadline:
            result = get_and_clear_pending_result(chat_id)
            if result is not None:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "confirmed": result.get("confirmed"),
                                    "rejected": result.get("rejected"),
                                    "reply": result.get("reply", ""),
                                }
                            ),
                        }
                    ]
                }
            time.sleep(0.5)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"confirmed": False, "timeout": True, "reply": ""}),
                }
            ]
        }

    if name == "get_user_feedback":
        feedback = pop_dev_feedback(chat_id)
        return {"content": [{"type": "text", "text": json.dumps(feedback)}]}

    return {"content": [{"type": "text", "text": f"Неизвестный инструмент: {name}"}]}


MCP_TOOLS_SPEC = [
    {
        "name": "notify",
        "description": "Отправить сообщение в Telegram.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
    {
        "name": "ask_confirmation",
        "description": "Запросить подтверждение в Telegram (confirm/reject). Таймаут по умолчанию 120 сек.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}, "timeout_sec": {"type": "integer"}},
            "required": ["message"],
        },
    },
    {
        "name": "get_user_feedback",
        "description": "Забрать сообщения от пользователя (/dev в Telegram).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _mcp_client_address():
    """IP клиента для MCP (учёт X-Forwarded-For за прокси)."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


@app.route("/mcp/v1/agent/<endpoint_id>", methods=["POST"])
def mcp_api_base_post(endpoint_id):
    """POST базового URL: JSON-RPC MCP (initialize, tools/list, tools/call) для Cursor."""
    client_addr = _mcp_client_address()
    chat_id = _mcp_api_auth(endpoint_id)
    if not chat_id:
        logger.info(
            "[MCP] POST /mcp/v1/agent/%s method=(auth failed) address=%s -> 401 Unauthorized",
            endpoint_id,
            client_addr,
        )
        return jsonify(
            {"jsonrpc": "2.0", "error": {"code": -32001, "message": "Unauthorized"}}
        ), 401
    data = request.get_json(silent=True) or {}
    method = data.get("method")
    params = data.get("params") or {}
    req_id = data.get("id")
    logger.info(
        "[MCP] POST /mcp/v1/agent/%s method=%s address=%s chat_id=%s",
        endpoint_id,
        method or "(empty)",
        client_addr,
        chat_id,
    )

    def reply(result=None, error=None):
        out = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            out["error"] = error
        else:
            out["result"] = result
        return jsonify(out)

    if method == "initialize":
        return reply(
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "assistant-mcp", "version": "0.1.0"},
            }
        )
    if method == "notified" and params.get("method") == "initialized":
        return reply()
    if method == "tools/list":
        return reply({"tools": MCP_TOOLS_SPEC})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        args_str = json.dumps(args, ensure_ascii=False)[:400].replace("\n", " ")
        try:
            result = _mcp_tools_call(chat_id, endpoint_id, name, args)
            resp_preview = ""
            if isinstance(result, dict) and "content" in result:
                for c in result.get("content", [])[:1]:
                    if isinstance(c, dict) and c.get("type") == "text":
                        t = (c.get("text") or "")[:300]
                        resp_preview = t.replace("\n", " ").strip()
                        break
            logger.info(
                "[MCP] tools/call endpoint_id=%s tool=%s address=%s request=%s -> response=%s",
                endpoint_id,
                name,
                client_addr,
                args_str or "{}",
                resp_preview or "(empty)",
            )
            return reply(result)
        except Exception as e:
            err_msg = str(e)[:300]
            logger.exception(
                "MCP tools/call endpoint_id=%s tool=%s error=%s",
                endpoint_id,
                name,
                err_msg,
            )
            logger.warning(
                "[MCP] tools/call endpoint_id=%s tool=%s address=%s request=%s -> error=%s",
                endpoint_id,
                name,
                client_addr,
                args_str or "{}",
                err_msg,
            )
            return reply(error={"code": -32603, "message": str(e)})
    return reply(error={"code": -32601, "message": f"Method not found: {method}"})


@app.route("/mcp/v1/agent/<endpoint_id>/notify", methods=["POST"])
def mcp_api_notify(endpoint_id):
    chat_id = _mcp_api_auth(endpoint_id)
    if not chat_id:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "message required"}), 400
    from assistant.core.notify import notify_to_chat

    ok = notify_to_chat(chat_id, message)
    return jsonify({"ok": ok})


@app.route("/mcp/v1/agent/<endpoint_id>/question", methods=["POST"])
def mcp_api_question(endpoint_id):
    chat_id = _mcp_api_auth(endpoint_id)
    if not chat_id:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "message required"}), 400
    from assistant.core.notify import notify_to_chat

    prompt = message + "\n\nОтветьте в Telegram (confirm/reject или свой текст)."
    ok = notify_to_chat(chat_id, prompt)
    return jsonify({"ok": ok})


@app.route("/mcp/v1/agent/<endpoint_id>/confirmation", methods=["POST"])
def mcp_api_confirmation(endpoint_id):
    chat_id = _mcp_api_auth(endpoint_id)
    if not chat_id:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "message required"}), 400
    from assistant.core.notify import send_confirmation_request

    ok = send_confirmation_request(chat_id, message)
    return jsonify(
        {
            "ok": ok,
            "pending": True,
            "message": "Сообщение с кнопками Подтвердить/Отклонить отправлено. Ожидайте ответ в SSE /events или в следующем запросе /replies.",
        }
    )


@app.route("/mcp/v1/agent/<endpoint_id>/replies", methods=["GET"])
def mcp_api_replies(endpoint_id):
    chat_id = _mcp_api_auth(endpoint_id)
    if not chat_id:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    from assistant.core.notify import pop_dev_feedback

    replies = pop_dev_feedback(chat_id)
    return jsonify({"ok": True, "replies": replies})


@app.route("/mcp/v1/agent/<endpoint_id>/events", methods=["GET"])
def mcp_api_events(endpoint_id):
    chat_id = _mcp_api_auth(endpoint_id)
    if not chat_id:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    from assistant.dashboard.mcp_endpoints import pop_mcp_events

    def event_stream():
        while True:
            events = pop_mcp_events(endpoint_id, timeout_sec=25.0)
            for ev in events:
                ev_type = ev.get("type", "")
                data = ev.get("data", {})
                yield f"event: {ev_type}\ndata: {json.dumps(data)}\n\n"
            if not events:
                yield ": keepalive\n\n"

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


_REPOS_BODY = """
<h1>Склонированные репозитории</h1>
<p class="sub">Список директорий с .git в workspace (путь задаётся через WORKSPACE_DIR или SANDBOX_WORKSPACE_DIR).</p>
<div class="card">
  <p class="hint" style="margin-bottom:0.5rem">Workspace: {{ workspace_dir or '— не задан —' }}</p>
  {% if repos %}
  <table style="width:100%%; border-collapse: collapse;">
    <thead>
      <tr style="text-align:left; border-bottom: 1px solid var(--border);">
        <th style="padding:0.5rem 0.75rem;">Директория</th>
        <th style="padding:0.5rem 0.75rem;">Remote (origin)</th>
      </tr>
    </thead>
    <tbody>
      {% for r in repos %}
      <tr style="border-bottom: 1px solid var(--border);">
        <td style="padding:0.5rem 0.75rem;"><code>{{ r.path }}</code></td>
        <td style="padding:0.5rem 0.75rem; word-break: break-all;">{{ r.remote_url or '—' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="hint">Репозиториев нет или workspace недоступен.</p>
  {% endif %}
</div>
"""


def _get_workspace_dir() -> str:
    """Путь к workspace для сканирования репо (дашборд/API)."""
    env = os.getenv("WORKSPACE_DIR", "").strip() or os.getenv("SANDBOX_WORKSPACE_DIR", "").strip()
    if env:
        return env
    try:
        cfg = get_config_from_redis_sync(get_redis_url())
        return (cfg.get("WORKSPACE_DIR") or "").strip()
    except Exception:
        return ""


@app.route("/repos")
def repos_page():
    config = load_config()
    workspace_dir = _get_workspace_dir()
    repos: list = []
    if workspace_dir:
        try:
            from assistant.skills.git import list_cloned_repos_sync

            repos = list_cloned_repos_sync(workspace_dir)
        except Exception:
            pass
    return render_template_string(
        INDEX_HTML.replace("{{ layout_css }}", LAYOUT_CSS).replace(
            "{% block content %}{% endblock %}", _REPOS_BODY
        ),
        config=config,
        section="repos",
        workspace_dir=workspace_dir or None,
        repos=repos,
    )


@app.route("/monitor")
def monitor():
    config = load_config()
    info = _redis_info()
    return render_template_string(
        INDEX_HTML.replace("{{ layout_css }}", LAYOUT_CSS).replace(
            "{% block content %}{% endblock %}", _MONITOR_BODY
        ),
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


def _normalize_base_url(base_url: str, for_lm_studio_native: bool) -> str:
    """OpenAI-compat base URL must end with /v1. LM Studio native uses root (no /v1)."""
    u = (base_url or "").strip().rstrip("/") or "http://localhost:11434"
    if for_lm_studio_native:
        return u[:-3] if u.endswith("/v1") else u
    if not u.endswith("/v1"):
        u = u + "/v1"
    return u


@app.route("/api/test-model", methods=["POST"])
def api_test_model():
    redis_url = get_redis_url()
    cfg = get_config_from_redis_sync(redis_url)
    base_url = (cfg.get("OPENAI_BASE_URL") or "").strip() or "http://localhost:11434/v1"
    model_name = (cfg.get("MODEL_NAME") or "").strip() or "llama3.2"
    api_key = (cfg.get("OPENAI_API_KEY") or "").strip() or "ollama"
    use_lm_studio_native = (cfg.get("LM_STUDIO_NATIVE") or "").lower() in ("true", "1", "yes")

    async def _check():
        if use_lm_studio_native:
            from assistant.models import lm_studio

            try:
                out = await lm_studio.generate_lm_studio(
                    base_url or "http://localhost:1234",
                    model_name,
                    "Hi",
                    api_key=api_key,
                )
                return True if (out and out.strip()) else "Empty response"
            except Exception as e:
                return _model_check_hint(str(e))
        normalized_base = _normalize_base_url(base_url, for_lm_studio_native=False)
        from openai import AsyncOpenAI

        http_client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0))
        client = AsyncOpenAI(
            base_url=normalized_base,
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


@app.route("/api/pairing-code", methods=["POST"])
def api_pairing_code():
    """Create one-time pairing code. Returns code, link (if bot username known), expires_in_sec."""
    redis_url = get_redis_url()
    try:
        code, expires = create_pairing_code(redis_url)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    link = None
    cfg = get_config_from_redis_sync(redis_url)
    token = (cfg.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if token:
        try:
            r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5.0)
            data = r.json()
            if data.get("ok"):
                username = data.get("result", {}).get("username", "")
                if username:
                    link = f"https://t.me/{username}?start={code}"
        except Exception:
            pass
    return jsonify({"ok": True, "code": code, "link": link, "expires_in_sec": expires})


@app.route("/api/cloned-repos", methods=["GET"])
def api_cloned_repos():
    """JSON: список склонированных репо в workspace (path, remote_url)."""
    workspace_dir = _get_workspace_dir()
    repos: list = []
    if workspace_dir:
        try:
            from assistant.skills.git import list_cloned_repos_sync

            repos = list_cloned_repos_sync(workspace_dir)
        except Exception:
            pass
    return jsonify({"ok": True, "repos": repos, "workspace_dir": workspace_dir or None})


@app.route("/api/monitor")
def api_monitor():
    return jsonify(_redis_info())


def main():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
