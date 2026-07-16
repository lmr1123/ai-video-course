#!/usr/bin/env python3
"""Tiny signed-cookie authentication service for Caddy forward_auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit


COOKIE_NAME = "briefing_session"


def safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/prototype/briefing/?batch=latest"
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return "/prototype/briefing/?batch=latest"
    return value


def sign_session(secret: str, expires_at: int) -> str:
    payload = str(expires_at).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def valid_session(secret: str, token: str | None, now: int | None = None) -> bool:
    if not token or "." not in token:
        return False
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        expires_at = int(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, UnicodeDecodeError):
        return False
    return expires_at > (int(time.time()) if now is None else now)


class SessionHandler(BaseHTTPRequestHandler):
    server_version = "BriefingSession/1.0"

    @property
    def settings(self) -> dict[str, object]:
        return self.server.settings  # type: ignore[attr-defined]

    def _cookie_token(self) -> str | None:
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return None
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def _authorized(self) -> bool:
        return valid_session(str(self.settings["secret"]), self._cookie_token())

    def _headers(self, status: int, content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _redirect(self, location: str, set_cookie: str | None = None) -> None:
        self._headers(302)
        self.send_header("Location", location)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()

    def _login_page(self, next_path: str, error: bool = False) -> None:
        message = '<p class="error">账号或密码不正确，请重试。</p>' if error else ""
        body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · 资讯速听</title><style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f5f4f1;color:#191918;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{width:min(400px,calc(100% - 32px));padding:32px;border-radius:24px;background:#fff;box-shadow:0 20px 60px #00000012}}h1{{margin:0 0 8px;font-size:25px}}p{{margin:0 0 24px;color:#6c6964;font-size:14px;line-height:1.6}}label{{display:block;margin:16px 0 7px;font-size:13px;font-weight:600}}input{{width:100%;height:48px;border:1px solid #d8d5cf;border-radius:12px;padding:0 14px;font-size:16px}}button{{width:100%;height:48px;margin-top:24px;border:0;border-radius:12px;background:#1d1d1f;color:#fff;font-size:16px;font-weight:650}}.error{{margin:12px 0;color:#b42318}}
</style></head><body><main><h1>资讯速听</h1><p>登录一次，这台设备将在 90 天内保持登录。</p>{message}
<form method="post" action="/auth/login"><input type="hidden" name="next" value="{html.escape(next_path, quote=True)}">
<label for="username">账号</label><input id="username" name="username" autocomplete="username" required autofocus>
<label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">登录并继续</button></form></main></body></html>"""
        data = body.encode()
        self._headers(200, "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/verify":
            if self._authorized():
                self._headers(200)
                self.end_headers()
                return
            original = safe_next(self.headers.get("X-Forwarded-Uri"))
            self._redirect(f"/auth/login?{urlencode({'next': original})}")
            return
        if parsed.path == "/login":
            next_path = safe_next(parse_qs(parsed.query).get("next", [None])[0])
            if self._authorized():
                self._redirect(next_path)
            else:
                self._login_page(next_path)
            return
        if parsed.path == "/logout":
            expired = f"{COOKIE_NAME}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax"
            self._redirect("/auth/login", expired)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/login":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 4096:
            self.send_error(400)
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        username = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
        next_path = safe_next(form.get("next", [None])[0])
        user_ok = hmac.compare_digest(username, str(self.settings["username"]))
        pass_ok = hmac.compare_digest(password, str(self.settings["password"]))
        if not (user_ok and pass_ok):
            self._login_page(next_path, error=True)
            return
        max_age = int(self.settings["session_days"]) * 86400
        token = sign_session(str(self.settings["secret"]), int(time.time()) + max_age)
        session_cookie = f"{COOKIE_NAME}={token}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax"
        self._redirect(next_path, session_cookie)


def load_settings() -> dict[str, object]:
    required = {
        "username": os.environ.get("BRIEFING_AUTH_USERNAME", ""),
        "password": os.environ.get("BRIEFING_AUTH_PASSWORD", ""),
        "secret": os.environ.get("BRIEFING_SESSION_SECRET", ""),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(f"Missing session settings: {', '.join(missing)}")
    required["session_days"] = int(os.environ.get("BRIEFING_SESSION_DAYS", "90"))
    return required


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8793), SessionHandler)
    server.settings = load_settings()  # type: ignore[attr-defined]
    server.serve_forever()


if __name__ == "__main__":
    main()
