"""Web dashboard: Telegram, Model, MCP, monitoring. Config in Redis. Auth: users/sessions in Redis."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys

import httpx

logger = logging.getLogger(__name__)
# Под gunicorn у root logger может не быть handler — логи приложения не выводятся.
# Явно пишем в stderr, чтобы --capture-output показывал в т.ч. [MCP].
if not logger.handlers:
    _stderr = logging.StreamHandler(sys.stderr)
    _stderr.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(_stderr)
    logger.setLevel(logging.INFO)
    logger.propagate = False
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
    list_users,
    require_role,
    setup_done,
    update_password,
    verify_user,
)
from assistant.dashboard.config_store import (
    MCP_SERVERS_KEY,
    PAIRING_MODE_KEY,
    REDIS_PREFIX,
    TELEGRAM_ADMIN_IDS_KEY,
    approve_telegram_user_sync,
    create_pairing_code,
    create_telegram_secret_sync,
    get_config_from_redis_sync,
    get_redis_url,
    list_telegram_pending_sync,
    list_telegram_secrets_sync,
    reject_telegram_user_sync,
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

# CSS вынесен в static/css/layout.css (UX_UI_ROADMAP 4.1)
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Assistant — Панель</title>
  <link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/layout.css') }}">
</head>
<body>
  <nav class="nav">
    <a href="{{ url_for('index') }}" class="{{ 'active' if section == 'channels' else '' }}">Каналы</a>
    <a href="{{ url_for('model') }}" class="{{ 'active' if section == 'model' else '' }}">Модель</a>
    <a href="{{ url_for('integrations_page') }}" class="{{ 'active' if section == 'integrations' else '' }}">Интеграции</a>
    <a href="{{ url_for('data_page') }}" class="{{ 'active' if section == 'data' else '' }}">Данные</a>
    <a href="{{ url_for('system_page') }}" class="{{ 'active' if section == 'system' else '' }}">Система</a>
    {% if current_user and current_user.role == 'owner' %}
    <a href="{{ url_for('users_page') }}" class="{{ 'active' if section == 'users' else '' }}">Пользователи</a>
    {% endif %}
    {% if current_user %}
    <span style="margin-left:auto;color:var(--muted);font-size:0.9rem">{{ current_user.display_name or current_user.login }} ({{ current_user.role }})</span>
    <a href="{{ url_for('change_password_page') }}" style="margin-left:0.5rem">Сменить пароль</a>
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
  <div id="toast-container"></div>
  <script src="{{ url_for('static', filename='js/app.js') }}"></script>
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
  <link rel="stylesheet" href="{{ url_for('static', filename='css/layout.css') }}">
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
  <link rel="stylesheet" href="{{ url_for('static', filename='css/layout.css') }}">
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
    if TELEGRAM_ADMIN_IDS_KEY in data and isinstance(data[TELEGRAM_ADMIN_IDS_KEY], list):
        data["TELEGRAM_ADMIN_IDS_STR"] = ",".join(str(x) for x in data[TELEGRAM_ADMIN_IDS_KEY])
    else:
        data["TELEGRAM_ADMIN_IDS_STR"] = data.get(TELEGRAM_ADMIN_IDS_KEY, "") or ""
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
    if path in ("/login", "/logout", "/api/session", "/api/health"):
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
            LOGIN_HTML,
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
        return render_template_string(SETUP_HTML)
    if setup_done(r):
        return redirect(url_for("index"))
    if request.method == "GET":
        return render_template_string(SETUP_HTML)
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
_TELEGRAM_INSTRUCTIONS = """
<details class="card" style="margin-bottom:1rem">
  <summary class="details-summary">Инструкция: привязка пользователей</summary>
  <ol style="margin:0.5rem 0; padding-left:1.2rem;">
    <li>Пользователь нажимает <b>Start</b> в боте или отправляет команду /start.</li>
    <li>В блоке «Ожидают одобрения» ниже появятся заявки с именами (User ID, имя, username).</li>
    <li>Одобрите или отклоните заявку кнопками.</li>
    <li>Либо сгенерируйте <b>секретный ключ</b> и передайте пользователю — он вводит в боте: <code>/start ВАШ_КЛЮЧ</code>. Ключ действует 7 дней.</li>
  </ol>
  <p class="hint">Комбинация: можно отправить пользователю сообщение с кнопкой Start и текстом «Выполните привязку: введите в боте /start ВАШ_КЛЮЧ» после генерации ключа.</p>
</details>
"""
_TELEGRAM_BODY = """
<h1>Telegram</h1>
<p class="sub">Токен бота и pairing. Разрешённые ID можно задать вручную, через одобрение заявок или секретный ключ.</p>
""" + _TELEGRAM_INSTRUCTIONS + """
<form method="post" action="/save-telegram" id="form-telegram">
  <div class="card">
    <label for="token">Bot Token</label>
    <input id="token" name="telegram_bot_token" type="password" value="{{ config.get('TELEGRAM_BOT_TOKEN', '') }}" placeholder="123456:ABC..." autocomplete="off">
    <p class="hint">Получить у @BotFather.</p>
    <button type="button" id="btn-test-bot" class="btn btn-secondary" style="margin-top:0.75rem" onclick="testBot()">Проверить бота</button>
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
    <label for="admin_ids">Админские User ID (для /restart)</label>
    <input id="admin_ids" name="telegram_admin_ids" type="text" value="{{ config.get('TELEGRAM_ADMIN_IDS_STR', '') }}" placeholder="123456789">
    <p class="hint">Через запятую. Только эти пользователи могут вызывать команду /restart в боте.</p>
  </div>
  <div class="card">
    <label for="dev_chat_id">Chat ID для MCP/агента (уведомления, confirm)</label>
    <input id="dev_chat_id" name="telegram_dev_chat_id" type="text" value="{{ config.get('TELEGRAM_DEV_CHAT_ID', '') }}" placeholder="123456789">
    <p class="hint">Куда слать сообщения от MCP-сервера (notify, ask_confirmation). Для личных чатов = User ID. Пусто — первый из разрешённых.</p>
    <p class="hint" style="margin-top:0.25rem">Текущий Chat ID для уведомлений MCP: <strong id="effective-dev-chat">{{ config.get('TELEGRAM_DEV_CHAT_ID', '') or 'не задано (используется первый из разрешённых)' }}</strong></p>
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
  <div class="card">
    <label>Ожидают одобрения</label>
    <p class="hint">Пользователи, нажавшие /start в боте. Одобрите или отклоните.</p>
    <div id="telegram-pending-list"></div>
    <button type="button" class="btn btn-secondary" onclick="refreshTelegramPending()" style="margin-top:0.5rem">Обновить</button>
  </div>
  <div class="card">
    <label>Секретные ключи привязки</label>
    <p class="hint">Передайте ключ пользователю — он вводит в боте: /start КЛЮЧ. Ключ одноразовый, действует 7 дней.</p>
    <button type="button" class="btn btn-secondary" onclick="genTelegramSecret()">Сгенерировать ключ</button>
    <span id="telegram-secret-result" style="margin-left:0.5rem;font-size:0.9rem"></span>
    <div id="telegram-secret-block" style="margin-top:0.75rem;display:none">
      <p class="hint">Ключ: <strong id="telegram-secret-key"></strong> — скопируйте и передайте пользователю.</p>
    </div>
    <div id="telegram-secrets-list" style="margin-top:0.75rem"></div>
  </div>
  <button type="submit" class="btn" id="btn-save-telegram">Сохранить</button>
</form>
<script>
(function() {
  var form = document.getElementById('form-telegram');
  if (form && window.apiPostForm && window.showToast) {
    form.addEventListener('submit', function(e) {
      e.preventDefault();
      var btn = document.getElementById('btn-save-telegram');
      if (btn) btn.disabled = true;
      window.apiPostForm('/save-telegram', form)
        .then(function(d) {
          if (d.success) { window.showToast('Сохранено.', 'success'); }
          else { window.showToast(d.error || 'Ошибка', 'error'); }
        })
        .catch(function(err) { window.showToast(err.message || 'Ошибка', 'error'); })
        .finally(function() { if (btn) btn.disabled = false; });
    });
  }
})();
function testBot() {
  var r = document.getElementById('bot-result');
  var btn = document.getElementById('btn-test-bot');
  r.textContent = '…';
  if (btn) btn.disabled = true;
  fetch('/api/test-bot', { method: 'POST' })
    .then(function(res) { return res.json(); })
    .then(function(d) { r.textContent = d.ok ? 'OK: ' + (d.username || '') : 'Ошибка: ' + (d.error || 'unknown'); })
    .catch(function(e) { r.textContent = 'Ошибка: ' + e.message; })
    .finally(function() { if (btn) btn.disabled = false; });
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
function refreshTelegramPending() {
  var el = document.getElementById('telegram-pending-list');
  if (!el) return;
  el.innerHTML = '…';
  fetch('/api/telegram-pending')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!d.ok) { el.innerHTML = '<p class="hint">Ошибка: ' + (d.error || '') + '</p>'; return; }
      var pending = d.pending || [];
      if (pending.length === 0) { el.innerHTML = '<p class="hint">Нет заявок.</p>'; return; }
      var html = '<table class="monitor-table" style="width:100%; border-collapse:collapse;"><thead><tr style="text-align:left"><th style="padding:0.4rem">User ID</th><th style="padding:0.4rem">Имя</th><th style="padding:0.4rem">username</th><th></th></tr></thead><tbody>';
      pending.forEach(function(p) {
        var name = [p.first_name, p.last_name].filter(Boolean).join(' ') || '—';
        var uname = p.username ? '@' + p.username : '—';
        html += '<tr><td style="padding:0.4rem">' + p.user_id + '</td><td style="padding:0.4rem">' + name + '</td><td style="padding:0.4rem">' + uname + '</td><td style="padding:0.4rem"><button type="button" class="btn btn-secondary" onclick="approveTelegramUser(' + p.user_id + ')">Одобрить</button> <button type="button" class="btn btn-secondary" onclick="rejectTelegramUser(' + p.user_id + ')">Отклонить</button></td></tr>';
      });
      html += '</tbody></table>';
      el.innerHTML = html;
    })
    .catch(function(e) { el.innerHTML = '<p class="hint">Ошибка: ' + e.message + '</p>'; });
}
function approveTelegramUser(uid) {
  fetch('/api/telegram-approve', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: uid }) })
    .then(function(r) { return r.json(); })
    .then(function(d) { if (window.showToast) window.showToast(d.ok ? 'Одобрено.' : (d.error || 'Ошибка'), d.ok ? 'success' : 'error'); if (d.ok) refreshTelegramPending(); })
    .catch(function(e) { if (window.showToast) window.showToast(e.message || 'Ошибка', 'error'); });
}
function rejectTelegramUser(uid) {
  fetch('/api/telegram-reject', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: uid }) })
    .then(function(r) { return r.json(); })
    .then(function(d) { if (window.showToast) window.showToast(d.ok ? 'Отклонено.' : (d.error || 'Ошибка'), d.ok ? 'success' : 'error'); if (d.ok) refreshTelegramPending(); })
    .catch(function(e) { if (window.showToast) window.showToast(e.message || 'Ошибка', 'error'); });
}
function genTelegramSecret() {
  var result = document.getElementById('telegram-secret-result');
  var block = document.getElementById('telegram-secret-block');
  if (result) result.textContent = '…';
  if (block) block.style.display = 'none';
  fetch('/api/telegram-secret', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!d.ok) { if (result) result.textContent = 'Ошибка: ' + (d.error || ''); return; }
      document.getElementById('telegram-secret-key').textContent = d.secret;
      if (block) block.style.display = 'block';
      if (result) result.textContent = 'Готово';
      if (window.showToast) window.showToast('Ключ создан. Передайте пользователю.', 'success');
      refreshTelegramSecrets();
    })
    .catch(function(e) { if (result) result.textContent = 'Ошибка: ' + e.message; });
}
function refreshTelegramSecrets() {
  var el = document.getElementById('telegram-secrets-list');
  if (!el) return;
  fetch('/api/telegram-secrets')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!d.ok) { el.innerHTML = ''; return; }
      var list = d.secrets || [];
      if (list.length === 0) { el.innerHTML = '<p class="hint">Нет активных ключей.</p>'; return; }
      el.innerHTML = '<p class="hint">Активные ключи: ' + list.map(function(s) { return s.secret_masked + ' (через ' + s.expires_in_sec + ' с)'; }).join(', ') + '</p>';
    })
    .catch(function() { el.innerHTML = ''; });
}
document.addEventListener('DOMContentLoaded', function() { refreshTelegramPending(); refreshTelegramSecrets(); });
</script>
"""

_CHANNELS_HR = '\n<hr style="margin:1.5rem 0; border:0; border-top:1px solid var(--border)">\n'


@app.route("/")
def index():
    """Каналы: Telegram + Email на одной странице (UX_UI_ROADMAP)."""
    config = load_config()
    content = _TELEGRAM_BODY + _CHANNELS_HR + _EMAIL_BODY
    return render_template_string(
        INDEX_HTML.replace("{% block content %}{% endblock %}", content),
        config=config,
        section="channels",
    )


def _wants_json() -> bool:
    """True if client expects JSON (fetch with Accept or X-Requested-With)."""
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
    )


@app.route("/save-telegram", methods=["POST"])
def save_telegram():
    redis_url = get_redis_url()
    token = (request.form.get("telegram_bot_token") or "").strip()
    if not token:
        if _wants_json():
            return jsonify({"success": False, "error": "Укажите токен бота."}), 400
        flash("Укажите токен бота.", "error")
        return redirect(url_for("index"))
    users_str = (request.form.get("telegram_allowed_user_ids") or "").strip()
    user_ids = [
        int(x.strip()) for x in re.split(r"[\s,]+", users_str) if x.strip() and x.strip().isdigit()
    ]
    admin_str = (request.form.get("telegram_admin_ids") or "").strip()
    admin_ids = [
        int(x.strip())
        for x in re.split(r"[\s,]+", admin_str)
        if x.strip() and x.strip().isdigit()
    ]
    pairing = request.form.get("pairing_mode") == "1"
    set_config_in_redis_sync(redis_url, "TELEGRAM_BOT_TOKEN", token)
    set_config_in_redis_sync(redis_url, "TELEGRAM_ALLOWED_USER_IDS", user_ids if user_ids else [])
    set_config_in_redis_sync(redis_url, TELEGRAM_ADMIN_IDS_KEY, admin_ids if admin_ids else [])
    set_config_in_redis_sync(redis_url, PAIRING_MODE_KEY, "true" if pairing else "false")
    set_config_in_redis_sync(
        redis_url, "TELEGRAM_DEV_CHAT_ID", (request.form.get("telegram_dev_chat_id") or "").strip()
    )
    if _wants_json():
        return jsonify({"success": True})
    flash("Сохранено. Настройки применяются автоматически.", "success")
    return redirect(url_for("index"))


# ----- Model -----
_MODEL_BODY = """
<h1>Модель</h1>
<p class="sub">Подключение к API (Ollama / OpenAI-совместимый). URL и ключ — затем загрузка списка моделей и выбор. Настройки применяются в дашборде и в Telegram.</p>
<form method="post" action="/save-model" id="form-model">
  <div class="card">
    <label for="base_url">URL API</label>
    <input id="base_url" name="openai_base_url" type="url" value="{{ config.get('OPENAI_BASE_URL', '') }}" placeholder="http://host.docker.internal:11434/v1">
    <p class="hint">Для Docker: host.docker.internal:11434. Локально: localhost:11434. Ollama: …/v1, LM Studio: часто …:1234/v1.</p>
  </div>
  <div class="card">
    <label for="api_key">API ключ</label>
    <input id="api_key" name="openai_api_key" type="password" value="{{ config.get('OPENAI_API_KEY', '') }}" placeholder="ollama или sk-..." autocomplete="off">
    <p class="hint">Для Ollama можно оставить «ollama» или пустым.</p>
  </div>
  <div class="card">
    <label for="model_select">Модель</label>
    <div class="row" style="flex-wrap:wrap;align-items:center;gap:0.5rem">
      <select id="model_select" style="min-width:12rem">
        <option value="{{ config.get('MODEL_NAME', '') or '' }}">{{ config.get('MODEL_NAME', '') or '— загрузите список —' }}</option>
      </select>
      <input type="hidden" id="model_name" name="model_name" value="{{ config.get('MODEL_NAME', '') or '' }}">
      <button type="button" id="btn-load-models" class="btn btn-secondary">Загрузить модели</button>
      <span id="model-load-status" style="font-size:0.9rem;color:var(--muted)"></span>
    </div>
    <p class="hint">Укажите URL и ключ выше, нажмите «Загрузить модели» — подставится первая модель из API. Выбор модели действует и в Telegram.</p>
  </div>
  <details class="card" style="margin-bottom:1rem">
    <summary class="details-summary">Дополнительно</summary>
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
  </details>
  <button type="submit" class="btn" id="btn-save-model">Сохранить</button>
  <button type="button" id="btn-test-model" class="btn btn-secondary" style="margin-left:0.5rem" onclick="testModel()">Проверить подключение</button>
  <span id="model-result" style="margin-left:0.5rem;font-size:0.9rem"></span>
</form>
<script>
(function() {
  var form = document.getElementById('form-model');
  var select = document.getElementById('model_select');
  var hidden = document.getElementById('model_name');
  var loadBtn = document.getElementById('btn-load-models');
  var loadStatus = document.getElementById('model-load-status');
  function syncModelToHidden() {
    if (select && hidden) hidden.value = (select.value || '').trim();
  }
  if (select) select.addEventListener('change', syncModelToHidden);

  if (loadBtn && form) {
    loadBtn.addEventListener('click', function() {
      loadStatus.textContent = 'Загрузка…';
      if (loadBtn.disabled === false) loadBtn.disabled = true;
      var fd = new FormData(form);
      var body = {
        openai_base_url: fd.get('openai_base_url') || '',
        openai_api_key: fd.get('openai_api_key') || '',
        lm_studio_native: fd.get('lm_studio_native') === '1'
      };
      fetch('/api/list-models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      })
        .then(function(res) { return res.json(); })
        .then(function(d) {
          if (d.error) {
            loadStatus.textContent = d.error;
            return;
          }
          var models = d.models || [];
          if (models.length === 0) {
            loadStatus.textContent = 'Модели не найдены';
            return;
          }
          select.innerHTML = '';
          var current = (hidden && hidden.value) ? hidden.value : '';
          var first = d.first || models[0];
          models.forEach(function(m) {
            var opt = document.createElement('option');
            opt.value = m;
            opt.textContent = m;
            if (m === current) opt.selected = true;
            select.appendChild(opt);
          });
          if (!current || models.indexOf(current) === -1) {
            select.value = first;
            if (hidden) hidden.value = first;
          } else {
            syncModelToHidden();
          }
          loadStatus.textContent = 'Загружено: ' + models.length + (first ? ', выбрана: ' + (select.value || first) : '');
        })
        .catch(function(e) {
          loadStatus.textContent = 'Ошибка: ' + (e.message || 'сеть');
        })
        .finally(function() { loadBtn.disabled = false; });
    });
  }

  if (form && window.apiPostForm && window.showToast) {
    form.addEventListener('submit', function(e) {
      e.preventDefault();
      syncModelToHidden();
      var btn = document.getElementById('btn-save-model');
      if (btn) btn.disabled = true;
      window.apiPostForm('/save-model', form)
        .then(function(d) {
          if (d.success) { window.showToast('Сохранено. Модель используется в дашборде и в Telegram.', 'success'); }
          else { window.showToast(d.error || 'Ошибка', 'error'); }
        })
        .catch(function(err) { window.showToast(err.message || 'Ошибка', 'error'); })
        .finally(function() { if (btn) btn.disabled = false; });
    });
  }
})();
function testModel() {
  var r = document.getElementById('model-result');
  var btn = document.getElementById('btn-test-model');
  r.textContent = '…';
  if (btn) btn.disabled = true;
  fetch('/api/test-model', { method: 'POST' })
    .then(function(res) { return res.json(); })
    .then(function(d) { r.textContent = d.ok ? 'OK' : 'Ошибка: ' + (d.error || 'unknown'); })
    .catch(function(e) { r.textContent = 'Ошибка: ' + e.message; })
    .finally(function() { if (btn) btn.disabled = false; });
}
</script>
"""


@app.route("/model")
def model():
    config = load_config()
    return render_template_string(
        INDEX_HTML.replace("{% block content %}{% endblock %}", _MODEL_BODY),
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
    if _wants_json():
        return jsonify({"success": True})
    flash("Сохранено. Настройки модели применяются автоматически.", "success")
    return redirect(url_for("model"))


# ----- Email -----
_EMAIL_BODY = """
<h1>Email</h1>
<p class="sub">Настройки канала отправки писем (для скилла send_email и ответов по каналу Email).</p>
<form method="post" action="/save-email" id="form-email">
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
  <button type="submit" class="btn" id="btn-save-email">Сохранить</button>
</form>
<script>
(function() {
  var form = document.getElementById('form-email');
  if (form && window.apiPostForm && window.showToast) {
    form.addEventListener('submit', function(e) {
      e.preventDefault();
      var btn = document.getElementById('btn-save-email');
      if (btn) btn.disabled = true;
      window.apiPostForm('/save-email', form)
        .then(function(d) {
          if (d.success) { window.showToast('Сохранено.', 'success'); }
          else { window.showToast(d.error || 'Ошибка', 'error'); }
        })
        .catch(function(err) { window.showToast(err.message || 'Ошибка', 'error'); })
        .finally(function() { if (btn) btn.disabled = false; });
    });
  }
})();
</script>
"""


@app.route("/email")
def email_settings():
    config = load_config()
    return render_template_string(
        INDEX_HTML.replace("{% block content %}{% endblock %}", _EMAIL_BODY),
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
    if _wants_json():
        return jsonify({"success": True})
    flash("Настройки Email сохранены.", "success")
    return redirect(url_for("email_settings"))


# ----- Память разговоров (итерация 8.3) -----
_MEMORY_BODY = """
<h1>Память разговоров</h1>
<p class="sub">Очистка данных в Qdrant (коллекция conversation_memory) по user_id и опционально chat_id.</p>
<form method="post" action="{{ url_for('clear_conversation_memory_post') }}">
  <div class="card">
    <label for="conv_user_id">User ID (Telegram)</label>
    <input id="conv_user_id" name="user_id" type="text" required placeholder="123456789">
    <p class="hint">Ваш Telegram User ID (для личного чата совпадает с Chat ID). Узнать: написать боту /start или посмотреть в настройках Telegram.</p>
  </div>
  <div class="card">
    <label for="conv_chat_id">Chat ID (опционально)</label>
    <input id="conv_chat_id" name="chat_id" type="text" placeholder="">
    <p class="hint">Очистить только один чат. Пусто — очистить всю память разговоров этого пользователя.</p>
  </div>
  <button type="submit" class="btn">Очистить мою память разговоров</button>
</form>
"""


# ----- Данные: Qdrant (единый источник), ссылки на Репо и Память (UX_UI_ROADMAP) -----
_DATA_BODY = """
<h1>Данные</h1>
<p class="sub">Векторная БД (Qdrant) для индексации документов и памяти разговоров. Репозитории и очистка памяти — ниже.</p>
<form method="post" action="{{ url_for('save_data') }}" id="form-data">
  <div class="card">
    <label for="qdrant_url">Qdrant URL</label>
    <input id="qdrant_url" name="qdrant_url" type="url" value="{{ config.get('QDRANT_URL', '') }}" placeholder="http://localhost:6333">
    <p class="hint">Используется для индексации документов (index_document, index_repo) и памяти разговоров.</p>
  </div>
  <button type="submit" class="btn" id="btn-save-data">Сохранить</button>
</form>
<script>
(function() {
  var form = document.getElementById('form-data');
  if (form && window.apiPostForm && window.showToast) {
    form.addEventListener('submit', function(e) {
      e.preventDefault();
      var btn = document.getElementById('btn-save-data');
      if (btn) btn.disabled = true;
      window.apiPostForm('/save-data', form)
        .then(function(d) {
          if (d.success) { window.showToast('Сохранено.', 'success'); }
          else { window.showToast(d.error || 'Ошибка', 'error'); }
        })
        .catch(function(err) { window.showToast(err.message || 'Ошибка', 'error'); })
        .finally(function() { if (btn) btn.disabled = false; });
    });
  }
})();
</script>
<hr style="margin:1.5rem 0; border:0; border-top:1px solid var(--border)">
<p><a href="{{ url_for('repos_page') }}">Репозитории</a> — токены GitHub/GitLab, workspace, склонированные репо.</p>
<p><a href="{{ url_for('memory_page') }}">Память разговоров</a> — очистка по user_id/chat_id.</p>
"""


@app.route("/data")
def data_page():
    config = load_config()
    return render_template_string(
        INDEX_HTML.replace("{% block content %}{% endblock %}", _DATA_BODY),
        config=config,
        section="data",
    )


@app.route("/save-data", methods=["POST"])
def save_data():
    redis_url = get_redis_url()
    qdrant_url = (request.form.get("qdrant_url") or "").strip()
    set_config_in_redis_sync(redis_url, "QDRANT_URL", qdrant_url)
    if _wants_json():
        return jsonify({"success": True})
    flash("Настройки данных сохранены.", "success")
    return redirect(url_for("data_page"))


@app.route("/memory")
def memory_page():
    config = load_config()
    return render_template_string(
        INDEX_HTML.replace("{% block content %}{% endblock %}", _MEMORY_BODY),
        config=config,
        section="memory",
    )


@app.route("/clear-conversation-memory", methods=["POST"])
def clear_conversation_memory_post():
    from assistant.core.qdrant_docs import clear_conversation_memory, get_qdrant_url

    redis_url = get_redis_url()
    user_id = (request.form.get("user_id") or "").strip()
    if not user_id:
        flash("Укажите User ID (Telegram).", "error")
        return redirect(url_for("memory_page"))
    chat_id = (request.form.get("chat_id") or "").strip() or None
    qdrant_url = get_qdrant_url(redis_url)
    ok, err = clear_conversation_memory(qdrant_url, user_id, chat_id=chat_id, redis_url=redis_url)
    if not ok:
        flash(err or "Не удалось очистить память разговоров.", "error")
    else:
        flash("Память разговоров очищена.", "success")
    return redirect(url_for("memory_page"))


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
        INDEX_HTML.replace("{% block content %}{% endblock %}", _MCP_BODY),
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
            return redirect(url_for("integrations_page"))
    if name and url:
        entry = {"name": name, "url": url}
        if args is not None:
            entry["args"] = args
        servers.append(entry)
        set_config_in_redis_sync(redis_url, MCP_SERVERS_KEY, servers)
        flash("MCP-сервер добавлен.", "success")
    return redirect(url_for("integrations_page"))


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
    return redirect(url_for("integrations_page"))


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
    <p class="hint">Куда слать уведомления и запросы подтверждения. Если не задан — используется общий Chat ID из раздела Каналы → Telegram.</p>
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


@app.route("/integrations")
def integrations_page():
    """Интеграции: MCP скиллы + MCP (агент) на одной странице (UX_UI_ROADMAP)."""
    from assistant.dashboard.mcp_endpoints import list_endpoints

    config = load_config()
    new_secret = session.pop("mcp_new_secret", None) if "mcp_new_secret" in session else None
    base_url = _mcp_agent_base_url()
    part_mcp = render_template_string(_MCP_BODY, config=config)
    part_agent = render_template_string(
        _MCP_AGENT_BODY,
        config=config,
        mcp_endpoints=list_endpoints(),
        new_secret=new_secret,
        base_url=base_url,
    )
    content = part_mcp + _CHANNELS_HR + part_agent
    return render_template_string(
        INDEX_HTML.replace("{% block content %}{% endblock %}", content),
        config=config,
        section="integrations",
    )


@app.route("/mcp-agent")
def mcp_agent():
    from assistant.dashboard.mcp_endpoints import list_endpoints

    config = load_config()
    new_secret = None
    if "mcp_new_secret" in session:
        new_secret = session.pop("mcp_new_secret", None)
    base_url = _mcp_agent_base_url()
    return render_template_string(
        INDEX_HTML.replace("{% block content %}{% endblock %}", _MCP_AGENT_BODY),
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
        return redirect(url_for("integrations_page"))
    endpoint_id, secret = create_endpoint(name, chat_id)
    base_url = _mcp_agent_base_url()
    session["mcp_new_secret"] = {"url": base_url + "mcp/v1/agent/" + endpoint_id, "secret": secret}
    flash("Endpoint создан. Скопируйте URL и секрет ниже.", "success")
    return redirect(url_for("integrations_page"))


@app.route("/mcp-agent/regenerate", methods=["POST"])
def mcp_agent_regenerate():
    from assistant.dashboard.mcp_endpoints import regenerate_endpoint_secret

    endpoint_id = (request.form.get("endpoint_id") or "").strip()
    if not endpoint_id:
        return redirect(url_for("integrations_page"))
    new_secret = regenerate_endpoint_secret(endpoint_id)
    if new_secret:
        session["mcp_new_secret"] = {
            "url": _mcp_agent_base_url() + "mcp/v1/agent/" + endpoint_id,
            "secret": new_secret,
        }
        flash("Секрет обновлён. Скопируйте новый секрет ниже.", "success")
    return redirect(url_for("integrations_page"))


@app.route("/mcp-agent/delete", methods=["POST"])
def mcp_agent_delete():
    from assistant.dashboard.mcp_endpoints import delete_endpoint

    endpoint_id = (request.form.get("endpoint_id") or "").strip()
    if endpoint_id:
        delete_endpoint(endpoint_id)
        flash("Endpoint удалён.", "success")
    return redirect(url_for("integrations_page"))


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

    if name == "create_task":
        title = (arguments.get("title") or "").strip()
        text = (arguments.get("text") or arguments.get("phrase") or "").strip()
        if not title and not text:
            return {"content": [{"type": "text", "text": json.dumps({"ok": False, "error": "Укажите title или text/phrase."})}]}
        user_id = str(chat_id)
        try:
            from assistant.skills.tasks import TaskSkill

            skill = TaskSkill()
            params = {"action": "create_task", "user_id": user_id}
            if title:
                params["title"] = title
            if text:
                params["text"] = text
            result = asyncio.run(skill.run(params))
            return {"content": [{"type": "text", "text": json.dumps(result)}]}
        except Exception as e:
            logger.exception("MCP create_task: %s", e)
            return {"content": [{"type": "text", "text": json.dumps({"ok": False, "error": str(e)})}]}

    if name == "list_tasks":
        user_id = str(chat_id)
        try:
            from assistant.skills.tasks import TaskSkill

            skill = TaskSkill()
            result = asyncio.run(
                skill.run({"action": "list_tasks", "user_id": user_id})
            )
            return {"content": [{"type": "text", "text": json.dumps(result)}]}
        except Exception as e:
            logger.exception("MCP list_tasks: %s", e)
            return {"content": [{"type": "text", "text": json.dumps({"ok": False, "error": str(e)})}]}

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
    {
        "name": "create_task",
        "description": "Создать задачу пользователя. Укажите title или text/phrase (например «завтра купить молоко» — парсер подставит срок).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "text": {"type": "string"},
                "phrase": {"type": "string"},
            },
        },
    },
    {
        "name": "list_tasks",
        "description": "Список задач пользователя (по chat_id endpoint'а).",
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
                "serverInfo": {"name": "assistant-mcp", "version": "0.2.1"},
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


# ----- Users (owner only) -----
_USERS_BODY = """
<h1>Пользователи</h1>
<p class="sub">Управление учётными записями Dashboard. Доступно только владельцу (owner).</p>
<div class="card">
  <table class="monitor-table" style="width:100%; max-width:500px; border-collapse:collapse;">
    <thead><tr style="text-align:left; border-bottom:1px solid var(--border);">
      <th style="padding:0.5rem;">Логин</th>
      <th style="padding:0.5rem;">Роль</th>
      <th style="padding:0.5rem;">Отображаемое имя</th>
    </tr></thead>
    <tbody>
    {% for u in users %}
    <tr style="border-bottom:1px solid var(--border);">
      <td style="padding:0.5rem;">{{ u.login }}</td>
      <td style="padding:0.5rem;">{{ u.role }}</td>
      <td style="padding:0.5rem;">{{ u.display_name or u.login }}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
<form method="post" action="{{ url_for('add_user') }}" id="form-add-user" style="margin-top:1rem;">
  <div class="card">
    <label for="new-login">Логин</label>
    <input type="text" id="new-login" name="login" required minlength="1" maxlength="64" placeholder="логин">
    <label for="new-password">Пароль</label>
    <input type="password" id="new-password" name="password" required minlength="1" placeholder="пароль">
    <label for="new-role">Роль</label>
    <select id="new-role" name="role">
      <option value="viewer">viewer</option>
      <option value="operator">operator</option>
      <option value="owner">owner</option>
    </select>
  </div>
  <button type="submit" class="btn">Добавить пользователя</button>
</form>
<h2 style="margin-top:1.5rem;">Сменить пароль пользователя</h2>
<p class="hint">Владелец может сбросить пароль любому пользователю (без ввода текущего пароля).</p>
<form method="post" action="{{ url_for('change_user_password') }}" style="margin-top:0.5rem;">
  <div class="card">
    <label for="ch-user-login">Логин</label>
    <select id="ch-user-login" name="login" required>
      {% for u in users %}
      <option value="{{ u.login }}">{{ u.login }} ({{ u.role }})</option>
      {% endfor %}
    </select>
    <label for="ch-user-new-password">Новый пароль</label>
    <input type="password" id="ch-user-new-password" name="new_password" required minlength="1" placeholder="новый пароль">
  </div>
  <button type="submit" class="btn">Сменить пароль</button>
</form>
"""

_CHANGE_PASSWORD_BODY = """
<h1>Сменить пароль</h1>
<p class="sub">Укажите текущий пароль и новый пароль (ROADMAP §1).</p>
<form method="post" action="{{ url_for('change_password_page') }}">
  <div class="card">
    <label for="current-password">Текущий пароль</label>
    <input type="password" id="current-password" name="current_password" required placeholder="текущий пароль">
    <label for="new-password">Новый пароль</label>
    <input type="password" id="new-password" name="new_password" required minlength="1" placeholder="новый пароль">
    <label for="new-password2">Повторите новый пароль</label>
    <input type="password" id="new-password2" name="new_password2" required minlength="1" placeholder="повторите">
  </div>
  <button type="submit" class="btn">Сменить пароль</button>
</form>
"""

# ----- Monitor -----
_MONITOR_BODY = """
<h1>Мониторинг</h1>
<p class="sub">Ресурсы Redis, ключи по типам, статус сервисов. <span id="monitor-updated" class="hint">Обновлено только что.</span></p>
<div class="monitor-grid">
  <div class="monitor-card"><div class="val" id="mem">{{ monitor.redis.get('used_memory_human', '—') }}</div><div class="label">Память Redis</div></div>
  <div class="monitor-card"><div class="val" id="clients">{{ monitor.redis.get('connected_clients', '—') }}</div><div class="label">Подключения</div></div>
  <div class="monitor-card"><div class="val" id="blocked">{{ monitor.redis.get('blocked_clients', '—') }}</div><div class="label">Заблокировано</div></div>
  <div class="monitor-card"><div class="val" id="tasks-total">{{ monitor.tasks.get('total', '—') }}</div><div class="label">Задач (оркестратор)</div></div>
  <div class="monitor-card"><div class="val" id="svc-model">{{ monitor.services.get('model', '—') }}</div><div class="label">Модель</div></div>
</div>
<table class="monitor-table" style="margin-top:1rem; width:100%%; max-width:400px; border-collapse:collapse;">
  <thead><tr style="text-align:left; border-bottom:1px solid var(--border);"><th style="padding:0.5rem;">Тип ключей</th><th style="padding:0.5rem;">Кол-во</th></tr></thead>
  <tbody id="keys-by-prefix">
  {% for label, count in monitor.keys_by_prefix.items() %}
  <tr style="border-bottom:1px solid var(--border);"><td style="padding:0.5rem;">{{ label }}</td><td style="padding:0.5rem;" data-label="{{ label }}">{{ count }}</td></tr>
  {% endfor %}
  </tbody>
</table>
<script>
(function(){
  var INTERVAL_MS = 10000;
  var updatedEl = document.getElementById('monitor-updated');
  function updateDOM(data){
    if (!data) return;
    if (data.redis) {
      document.getElementById('mem').textContent = data.redis.used_memory_human || '—';
      document.getElementById('clients').textContent = data.redis.connected_clients ?? '—';
      document.getElementById('blocked').textContent = data.redis.blocked_clients ?? '—';
    }
    if (data.tasks && data.tasks.total !== undefined) document.getElementById('tasks-total').textContent = data.tasks.total;
    if (data.services && data.services.model !== undefined) document.getElementById('svc-model').textContent = data.services.model;
    if (data.keys_by_prefix) for (var label in data.keys_by_prefix) { var cell = document.querySelector('[data-label="'+label+'"]'); if (cell) cell.textContent = data.keys_by_prefix[label]; }
  }
  function refresh(){
    fetch('/api/monitor').then(function(r){ return r.json(); }).then(function(data){ updateDOM(data); if(updatedEl) updatedEl.textContent = 'Обновлено только что. Следующее через 10 сек.'; }).catch(function(){ if(updatedEl) updatedEl.textContent = 'Ошибка обновления.'; });
  }
  setInterval(refresh, INTERVAL_MS);
})();
</script>
"""


_REPOS_BODY = """
<h1>Репозитории</h1>
<p class="sub">Авторизация GitHub/GitLab и путь для клонирования. После сохранения перезапустите assistant-core, чтобы подхватить токены и путь.</p>
<form method="post" action="{{ url_for('save_repos') }}">
  <div class="card">
    <label for="github_token">GitHub Token (для поиска и PR)</label>
    <input id="github_token" name="github_token" type="password" placeholder="ghp_... (оставьте пустым, чтобы не менять)" autocomplete="off">
    <p class="hint">Settings → Developer settings → Personal access tokens. Нужны права repo (для clone/PR) и read:user.</p>
  </div>
  <div class="card">
    <label for="gitlab_token">GitLab Token (для поиска и MR)</label>
    <input id="gitlab_token" name="gitlab_token" type="password" placeholder="glpat-... (оставьте пустым, чтобы не менять)" autocomplete="off">
    <p class="hint">Preferences → Access Tokens. Нужны read_repository, api.</p>
  </div>
  <div class="card">
    <label for="git_workspace_dir">Путь для клонирования (workspace)</label>
    <input id="git_workspace_dir" name="git_workspace_dir" type="text" value="{{ config.get('GIT_WORKSPACE_DIR', '') }}" placeholder="/workspace или /git_repos">
    <p class="hint">Пусто — используется общий workspace (/workspace). Для отдельного тома укажите путь (напр. /git_repos) и смонтируйте его в Docker.</p>
  </div>
  <p class="hint">Qdrant URL настраивается в разделе <a href="{{ url_for('data_page') }}">Данные</a>.</p>
  <button type="submit" class="btn">Сохранить</button>
</form>
<hr style="margin:1.5rem 0; border:0; border-top:1px solid var(--border);">
<h2>Склонированные репозитории</h2>
<p class="sub">Директории с .git в выбранном workspace.</p>
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
    """Путь к workspace для сканирования репо (дашборд/API). Приоритет: GIT_WORKSPACE_DIR из Redis, затем WORKSPACE_DIR, затем env."""
    try:
        cfg = get_config_from_redis_sync(get_redis_url())
        git_ws = (cfg.get("GIT_WORKSPACE_DIR") or "").strip()
        if git_ws:
            return git_ws
        ws = (cfg.get("WORKSPACE_DIR") or "").strip()
        if ws:
            return ws
    except Exception:
        pass
    return (
        os.getenv("GIT_WORKSPACE_DIR", "").strip()
        or os.getenv("WORKSPACE_DIR", "").strip()
        or os.getenv("SANDBOX_WORKSPACE_DIR", "").strip()
    )


@app.route("/save-repos", methods=["POST"])
def save_repos():
    """Сохранить GITHUB_TOKEN, GITLAB_TOKEN, GIT_WORKSPACE_DIR в Redis. Qdrant — в разделе Данные."""
    redis_url = get_redis_url()
    github = (request.form.get("github_token") or "").strip()
    gitlab = (request.form.get("gitlab_token") or "").strip()
    git_workspace = (request.form.get("git_workspace_dir") or "").strip()
    if github:
        set_config_in_redis_sync(redis_url, "GITHUB_TOKEN", github)
    if gitlab:
        set_config_in_redis_sync(redis_url, "GITLAB_TOKEN", gitlab)
    set_config_in_redis_sync(redis_url, "GIT_WORKSPACE_DIR", git_workspace)
    flash(
        "Настройки репозиториев сохранены. Перезапустите assistant-core для применения токенов и пути.",
        "success",
    )
    return redirect(url_for("repos_page"))


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
        INDEX_HTML.replace("{% block content %}{% endblock %}", _REPOS_BODY),
        config=config,
        section="repos",
        workspace_dir=workspace_dir or None,
        repos=repos,
    )


@app.route("/users")
@require_role("owner")
def users_page():
    """Пользователи: список и добавление (только owner). ROADMAP §1."""
    r = get_redis()
    users = list_users(r)
    config = load_config()
    return render_template_string(
        INDEX_HTML.replace("{% block content %}{% endblock %}", _USERS_BODY),
        config=config,
        section="users",
        users=users,
    )


@app.route("/add-user", methods=["POST"])
@require_role("owner")
def add_user():
    """Добавить пользователя (только owner)."""
    login_name = (request.form.get("login") or "").strip()
    password = request.form.get("password") or ""
    role = (request.form.get("role") or "viewer").strip().lower()
    if role not in ("owner", "operator", "viewer"):
        role = "viewer"
    if not login_name or not password:
        flash("Укажите логин и пароль.", "error")
        return redirect(url_for("users_page"))
    r = get_redis()
    try:
        create_user(r, login_name, password, role=role)
        flash(f"Пользователь «{login_name}» создан.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("users_page"))


@app.route("/change-password", methods=["GET", "POST"])
def change_password_page():
    """Страница смены своего пароля (текущий + новый). ROADMAP §1."""
    if request.method == "GET":
        config = load_config()
        return render_template_string(
            INDEX_HTML.replace("{% block content %}{% endblock %}", _CHANGE_PASSWORD_BODY),
            config=config,
            section="change_password",
        )
    # POST
    current = request.form.get("current_password") or ""
    new1 = request.form.get("new_password") or ""
    new2 = request.form.get("new_password2") or ""
    if not current or not new1:
        flash("Укажите текущий и новый пароль.", "error")
        return redirect(url_for("change_password_page"))
    if new1 != new2:
        flash("Новый пароль и повтор не совпадают.", "error")
        return redirect(url_for("change_password_page"))
    r = get_redis()
    user = get_current_user(r)
    if not user:
        flash("Сессия не найдена. Войдите снова.", "error")
        return redirect(url_for("login"))
    if not verify_user(r, user["login"], current):
        flash("Неверный текущий пароль.", "error")
        return redirect(url_for("change_password_page"))
    try:
        update_password(r, user["login"], new1)
        flash("Пароль изменён.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("change_password_page"))


@app.route("/change-user-password", methods=["POST"])
@require_role("owner")
def change_user_password():
    """Сменить пароль пользователю (только owner, без текущего пароля)."""
    login_name = (request.form.get("login") or "").strip()
    new_password = request.form.get("new_password") or ""
    if not login_name or not new_password:
        flash("Укажите логин и новый пароль.", "error")
        return redirect(url_for("users_page"))
    r = get_redis()
    try:
        update_password(r, login_name, new_password)
        flash(f"Пароль для «{login_name}» изменён.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("users_page"))


@app.route("/system")
def system_page():
    """Система: мониторинг (UX_UI_ROADMAP)."""
    config = load_config()
    monitor_data = _monitor_data()
    return render_template_string(
        INDEX_HTML.replace("{% block content %}{% endblock %}", _MONITOR_BODY),
        config=config,
        section="system",
        monitor=monitor_data,
    )


@app.route("/monitor")
def monitor():
    """Редирект для обратной совместимости: /monitor → /system."""
    return redirect(url_for("system_page"))


# Префиксы Redis для мониторинга (ключи по типам)
_MONITOR_KEY_PREFIXES = [
    ("assistant:task:", "Задачи (оркестратор)"),
    ("assistant:config:", "Конфиг"),
    ("assistant:session:", "Сессии"),
    ("assistant:user:", "Пользователи"),
    ("assistant:pairing:", "Коды привязки"),
    ("assistant:tasks:", "Задачи (skills)"),
    ("assistant:summary:", "Память (summary)"),
    ("assistant:short:", "Память (short)"),
    ("assistant:file_ref:", "Ссылки на файлы"),
]


def _monitor_services(redis_url: str) -> dict:
    """Проверка доступности сервисов (модель — по конфигу из Redis)."""
    out = {"dashboard": "ok"}
    try:
        cfg = get_config_from_redis_sync(redis_url)
        base_url = (cfg.get("OPENAI_BASE_URL") or "").strip().rstrip(
            "/"
        ) or "http://localhost:11434"
        if not base_url.endswith("/v1"):
            base_url = base_url + "/v1"
        use_lm = (cfg.get("LM_STUDIO_NATIVE") or "").lower() in ("true", "1", "yes")
        if use_lm:
            base_url = (cfg.get("OPENAI_BASE_URL") or "").strip().rstrip(
                "/"
            ) or "http://localhost:1234"
        r = httpx.get(base_url, timeout=2.0)
        out["model"] = "ok" if r.status_code < 500 else "error"
    except Exception:
        out["model"] = "error"
    return out


def _redis_info() -> dict:
    """Базовая структура для обратной совместимости (memory, clients, keys)."""
    try:
        import redis

        client = redis.from_url(get_redis_url(), decode_responses=True)
        raw = client.info("memory")
        raw["connected_clients"] = client.info("clients").get("connected_clients", 0)
        raw["blocked_clients"] = client.info("clients").get("blocked_clients", 0)
        keys = len(client.keys(REDIS_PREFIX + "*"))
        raw["keys"] = keys
        client.close()
        return raw
    except Exception:
        return {}


def _monitor_data() -> dict:
    """Расширенные данные для /api/monitor: redis (в т.ч. по префиксам), services, tasks."""
    redis_url = get_redis_url()
    result = {"redis": {}, "services": {}, "tasks": {}, "keys_by_prefix": {}}
    try:
        import redis

        client = redis.from_url(redis_url, decode_responses=True)
        mem = client.info("memory")
        cli = client.info("clients")
        result["redis"] = {
            "used_memory_human": mem.get("used_memory_human", "—"),
            "used_memory_peak_human": mem.get("used_memory_peak_human") or "—",
            "mem_fragmentation_ratio": mem.get("mem_fragmentation_ratio"),
            "connected_clients": cli.get("connected_clients", 0),
            "blocked_clients": cli.get("blocked_clients", 0),
        }
        for prefix, label in _MONITOR_KEY_PREFIXES:
            try:
                n = len(client.keys(prefix + "*"))
                result["keys_by_prefix"][label] = n
            except Exception:
                result["keys_by_prefix"][label] = "—"
        # Задачи оркестратора (активные)
        try:
            task_keys = client.keys("assistant:task:*")
            result["tasks"]["total"] = len(task_keys)
        except Exception:
            result["tasks"]["total"] = 0
        client.close()
    except Exception:
        result["redis"] = {"error": "no connection"}
    try:
        result["services"] = _monitor_services(redis_url)
    except Exception:
        result["services"] = {"dashboard": "ok", "model": "error"}
    return result


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


def _fetch_models_openai(base_url: str, api_key: str, timeout: float = 10.0) -> list[str]:
    """GET /v1/models (OpenAI-compatible). Returns list of model ids."""
    url = (base_url.rstrip("/") + "/models") if base_url else ""
    if not url:
        return []
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = httpx.get(url, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return []
        data = r.json()
        out = []
        for item in (data.get("data") or data.get("models")) or []:
            if isinstance(item, dict):
                mid = item.get("id") or item.get("name")
                if mid and isinstance(mid, str):
                    out.append(mid)
        return out
    except Exception:
        return []


def _fetch_models_ollama(root_url: str, timeout: float = 10.0) -> list[str]:
    """GET /api/tags (Ollama). root_url without /v1. Returns list of model names."""
    u = (root_url or "").strip().rstrip("/") or "http://localhost:11434"
    if u.endswith("/v1"):
        u = u[:-3].rstrip("/")
    url = u + "/api/tags"
    try:
        r = httpx.get(url, timeout=timeout)
        if r.status_code != 200:
            return []
        data = r.json()
        out = []
        for item in (data.get("models") or []):
            if isinstance(item, dict):
                name = item.get("name")
                if name and isinstance(name, str):
                    out.append(name)
        return out
    except Exception:
        return []


@app.route("/api/list-models", methods=["POST"])
def api_list_models():
    """Return list of model ids/names for given API URL and key. Uses OpenAI /models then Ollama /api/tags."""
    if request.is_json:
        body = request.get_json() or {}
        base_url = (body.get("openai_base_url") or "").strip() or "http://localhost:11434/v1"
        api_key = (body.get("openai_api_key") or "").strip() or "ollama"
        lm_native = (body.get("lm_studio_native") or "").lower() in ("true", "1", "yes")
    else:
        base_url = (request.form.get("openai_base_url") or "").strip() or "http://localhost:11434/v1"
        api_key = (request.form.get("openai_api_key") or "").strip() or "ollama"
        lm_native = (request.form.get("lm_studio_native") or "").lower() in ("true", "1", "yes")

    normalized = _normalize_base_url(base_url, for_lm_studio_native=lm_native)
    if lm_native:
        openai_base = normalized + "/v1" if not normalized.endswith("/v1") else normalized
        models = _fetch_models_openai(openai_base, api_key)
    else:
        models = _fetch_models_openai(normalized, api_key)
        if not models:
            root = normalized[:-3].rstrip("/") if normalized.endswith("/v1") else normalized
            models = _fetch_models_ollama(root)
    if models:
        return jsonify({"models": models, "first": models[0], "error": None})
    return jsonify({"models": [], "first": None, "error": "Не удалось получить список моделей. Проверьте URL и доступность API."})


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
        try:
            client = AsyncOpenAI(
                base_url=normalized_base,
                api_key=api_key,
                http_client=http_client,
            )
            r = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            return bool(r.choices)
        except Exception as e:
            return _model_check_hint(str(e))
        finally:
            await http_client.aclose()

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


@app.route("/api/telegram-pending", methods=["GET"])
def api_telegram_pending():
    """Список пользователей Telegram, ожидающих одобрения (нажали /start)."""
    redis_url = get_redis_url()
    try:
        pending = list_telegram_pending_sync(redis_url)
        return jsonify({"ok": True, "pending": pending})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/telegram-approve", methods=["POST"])
def api_telegram_approve():
    """Одобрить пользователя: добавить в разрешённые, убрать из pending."""
    redis_url = get_redis_url()
    data = request.get_json(silent=True) or {}
    user_id = request.form.get("user_id") or data.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "user_id required"}), 400
    try:
        approve_telegram_user_sync(redis_url, int(user_id))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/telegram-reject", methods=["POST"])
def api_telegram_reject():
    """Отклонить заявку пользователя."""
    redis_url = get_redis_url()
    data = request.get_json(silent=True) or {}
    user_id = request.form.get("user_id") or data.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "user_id required"}), 400
    try:
        reject_telegram_user_sync(redis_url, int(user_id))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/telegram-secret", methods=["POST"])
def api_telegram_secret():
    """Сгенерировать секретный ключ привязки. Возвращает ключ (показать один раз)."""
    redis_url = get_redis_url()
    try:
        key, ttl = create_telegram_secret_sync(redis_url)
        return jsonify({"ok": True, "secret": key, "expires_in_sec": ttl})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/telegram-secrets", methods=["GET"])
def api_telegram_secrets():
    """Список активных секретных ключей (маскированные)."""
    redis_url = get_redis_url()
    try:
        secrets = list_telegram_secrets_sync(redis_url)
        return jsonify({"ok": True, "secrets": secrets})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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


@app.route("/api/health")
def api_health():
    """Эндпоинт живости для мониторинга и балансировщиков (ROADMAP 3.1). Без авторизации."""
    return jsonify({"ok": True})


@app.route("/api/monitor")
def api_monitor():
    return jsonify(_monitor_data())


def main():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
