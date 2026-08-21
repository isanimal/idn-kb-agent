"""Persistent-session authentication detection without handling credentials."""

import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.kb.models import AuthState


def classify_auth_page(html: str, url: str) -> AuthState:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).lower().split())
    password = soup.select_one('input[type="password"]')
    login_form = password or soup.select_one('form[action*="login" i], input[name*="email" i], input[name*="username" i]')
    unauth_text = any(x in text for x in ("sign in", "masuk ke akun", "login", "session expired"))
    path = urlsplit(url).path.lower()
    if login_form and (unauth_text or "login" in path or "auth" in path): return AuthState.AUTH_REQUIRED
    authenticated_markers = soup.select_one('a[href*="/kb/training"], nav, aside, [class*="sidebar" i], [data-sidebar]')
    auth_text = any(x in text for x in ("knowledge based", "product training", "produk training", "peraturan", "lokasi training"))
    if authenticated_markers and auth_text: return AuthState.AUTHENTICATED
    return AuthState.UNKNOWN


def wait_for_manual_auth(page, timeout_seconds: int = 300, poll_seconds: float = 2.0) -> AuthState:
    print("Login ke kb.idn.id secara manual pada browser yang terbuka.")
    print("Sistem menunggu autentikasi selesai.")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            state = classify_auth_page(page.content(), page.url)
        except Exception:
            page.wait_for_timeout(int(poll_seconds * 1000))
            continue
        if state == AuthState.AUTHENTICATED: return AuthState.AUTH_RESTORED
        page.wait_for_timeout(int(poll_seconds * 1000))
    return AuthState.AUTH_FAILED
