# -*- coding: utf-8 -*-
"Cookie session login for the public demo (stdlib only)."
import hmac
import json
import os
import secrets
import threading
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSIONS = {}
FAILS = {}
LOCK = threading.Lock()
TTL = 7 * 24 * 3600
COOKIE = "rd_sid"


def _load():
    user = (os.environ.get("RD_AUTH_USER") or "").strip()
    pwd = os.environ.get("RD_AUTH_PASSWORD") or ""
    try:
        with open(os.path.join(APP, "config.json"), "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    if not user:
        user = (cfg.get("auth_user") or "").strip()
    if not pwd:
        pwd = cfg.get("auth_password") or ""
    return user, pwd


def enabled():
    u, p = _load()
    return bool(u and p)


def _ok(user, pwd):
    u, p = _load()
    if not (u and p):
        return False
    a = hmac.compare_digest((user or "").encode("utf-8"), u.encode("utf-8"))
    b = hmac.compare_digest((pwd or "").encode("utf-8"), p.encode("utf-8"))
    return a and b


def _token(handler):
    raw = handler.headers.get("Cookie") or ""
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v
    return ""


def _user_of(handler):
    tok = _token(handler)
    if not tok:
        return ""
    with LOCK:
        item = SESSIONS.get(tok)
        if not item:
            return ""
        if item[1] < time.time():
            SESSIONS.pop(tok, None)
            return ""
        return item[0]


def _new(user):
    tok = secrets.token_urlsafe(32)
    with LOCK:
        SESSIONS[tok] = [user, time.time() + TTL]
    return tok


def _drop(handler):
    tok = _token(handler)
    if tok:
        with LOCK:
            SESSIONS.pop(tok, None)


def _ip(handler):
    try:
        return handler.client_address[0]
    except Exception:
        return "-"


def _locked(ip):
    with LOCK:
        item = FAILS.get(ip)
        return bool(item and item[1] > time.time())


def _fail(ip):
    now = time.time()
    with LOCK:
        cnt, until = FAILS.get(ip, [0, 0.0])
        cnt = cnt + 1 if until > now else 1
        FAILS[ip] = [cnt, now + 300 if cnt >= 5 else 0.0]


def _redirect(handler, loc):
    handler.send_response(302)
    handler.send_header("Location", loc)
    if getattr(handler, "_cookie", ""):
        handler.send_header("Set-Cookie", handler._cookie)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def _clear(handler):
    handler._cookie = COOKIE + "=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def handle(handler, path, body):
    if path == "/api/logout":
        _drop(handler)
        _clear(handler)
        handler._send(200, {"ok": True})
        return True
    if path != "/api/login":
        return False
    ip = _ip(handler)
    if _locked(ip):
        handler._send(429, {"ok": False, "error": "尝试次数过多，请 5 分钟后再试"})
        return True
    user = str(body.get("username") or "")
    pwd = str(body.get("password") or "")
    if not _ok(user, pwd):
        _fail(ip)
        handler._send(401, {"ok": False, "error": "用户名或密码错误"})
        return True
    tok = _new(user.strip())
    handler._cookie = COOKIE + "=" + tok + "; Path=/; Max-Age=" + str(TTL) + "; HttpOnly; SameSite=Lax"
    handler._send(200, {"ok": True, "user": user.strip()})
    return True


def ensure(handler, path):
    if path in ("/login", "/login.html"):
        handler._static("login.html")
        return False
    if path == "/logout":
        _drop(handler)
        _clear(handler)
        _redirect(handler, "/login")
        return False
    if not enabled():
        return True
    if _user_of(handler):
        return True
    if path == "/favicon.ico":
        handler._send(404, {"error": "not found"})
        return False
    if path.startswith("/api/"):
        handler._send(401, {"ok": False, "error": "未登录或会话已过期", "login": True})
        return False
    _redirect(handler, "/login")
    return False
