"""Login: HOSxP account (user/password) or MOPH ID (moph.id.th Health ID + Provider ID).

Sessions are random tokens in an HttpOnly cookie; only a SHA-256 of the token is stored.
HOSxP is only read: the password is checked against opduser.passweb (MD5, as HOSxP stores it).
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any, Callable

from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import Settings
from .hosxp import HosxpError
from .store import Store
from .validate import text

log = logging.getLogger("ssop.auth")
COOKIE = "ssop_session"
STATE_COOKIE = "ssop_moph_state"
CALLBACK_PATHS = ("/auth/moph/callback", "/auth/healthid/callback")
PUBLIC_PATHS = {"/login", "/auth/login", "/auth/moph/start", *CALLBACK_PATHS, "/static/app.css", "/static/login.js"}
MAX_FAILURES = 5
# Paths under HEALTHID_BASE_URL (https://moph.id.th) and PROVIDERID_API_URL (https://provider.id.th).
MOPH_AUTHORIZE = "https://moph.id.th/oauth/redirect"
MOPH_TOKEN = "https://moph.id.th/api/v1/token"
PROVIDER_TOKEN = "https://provider.id.th/api/v1/services/token"
PROVIDER_PROFILE = "https://provider.id.th/api/v1/services/profile"


def _url(settings: Settings, default: str) -> str:
    """Swap the default host for the one configured in .env."""
    for base, configured in (("https://moph.id.th", settings.health_id_base_url),
                             ("https://provider.id.th", settings.provider_id_base_url)):
        if default.startswith(base):
            return configured + default[len(base):]
    return default
LOGIN_ERRORS = {
    "moph_off": "ยังไม่ได้ตั้งค่า MOPH ID ใน .env",
    "moph_state": "การเข้าสู่ระบบด้วย MOPH ID หมดเวลาหรือไม่ถูกต้อง กรุณาลองใหม่",
    "moph_denied": "ยกเลิกการเข้าสู่ระบบด้วย MOPH ID",
    "moph_failed": "เชื่อมต่อ MOPH ID ไม่สำเร็จ กรุณาลองใหม่",
    "moph_hospital": "บัญชี MOPH ID นี้ไม่ได้สังกัดโรงพยาบาลที่อนุญาต",
    "expired": "หมดเวลาการใช้งาน กรุณาเข้าสู่ระบบใหม่",
}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_matches(password: str, stored: str) -> bool:
    """HOSxP keeps MD5(password) in opduser.passweb. Thai passwords are hashed as TIS-620."""
    stored = text(stored).lower()
    if len(stored) != 32:
        return False
    candidates = []
    for encoding in ("cp874", "utf-8"):
        try:
            candidates.append(hashlib.md5(password.encode(encoding)).hexdigest())
        except UnicodeEncodeError:
            continue
    return any(hmac.compare_digest(c, stored) for c in candidates)


# ----------------------------------------------------------------- MOPH ID
def _http_json(url: str, *, form: dict | None = None, body: dict | None = None,
               headers: dict | None = None, timeout: float = 15, user_agent: str = "EMR") -> dict:
    # The program names itself (MOPH_USER_AGENT, default "EMR") instead of Python's default client name.
    data, all_headers = None, {"Accept": "application/json", "User-Agent": user_agent, **(headers or {})}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        all_headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        all_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=all_headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URLs
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Keep the server's own error text for the login log (it never contains our secrets).
        exc.detail = exc.read(400).decode("utf-8", "replace").replace("\n", " ")
        raise


def moph_authorize_url(settings: Settings, state: str) -> str:
    return _url(settings, MOPH_AUTHORIZE) + "?" + urllib.parse.urlencode({
        "client_id": settings.moph_health_id_client, "redirect_uri": settings.moph_redirect_uri,
        "response_type": "code", "state": state,
    })


def moph_profile(settings: Settings, code: str, fetch: Callable[..., dict] = _http_json) -> dict:
    """Exchange the code: Health ID token -> Provider ID token -> provider profile."""
    if fetch is _http_json:
        fetch = functools.partial(_http_json, user_agent=settings.moph_user_agent)
    health = fetch(_url(settings, MOPH_TOKEN), form={
        "grant_type": "authorization_code", "code": code, "redirect_uri": settings.moph_redirect_uri,
        "client_id": settings.moph_health_id_client, "client_secret": settings.moph_health_id_secret,
    })
    health_token = text((health.get("data") or {}).get("access_token"))
    if not health_token:
        raise ValueError("no Health ID token")
    provider = fetch(_url(settings, PROVIDER_TOKEN), body={
        "client_id": settings.moph_provider_id_client, "secret_key": settings.moph_provider_id_secret,
        "token_by": "Health ID", "token": health_token,
    })
    provider_token = text((provider.get("data") or {}).get("access_token"))
    if not provider_token:
        raise ValueError("no Provider ID token")
    profile = fetch(_url(settings, PROVIDER_PROFILE), headers={
        "Authorization": f"Bearer {provider_token}",
        "client-id": settings.moph_provider_id_client, "secret-key": settings.moph_provider_id_secret,
    })
    data = profile.get("data") or {}
    if not text(data.get("provider_id")) and not text(data.get("account_id")):
        raise ValueError("no provider profile")
    return data


def allowed_hospital(profile: dict, hcodes: tuple[str, ...]) -> bool:
    wanted = {h.strip() for h in hcodes if h.strip()}
    return any(text(o.get("hcode")) in wanted for o in profile.get("organization") or [])


# -------------------------------------------------------------------- app
def register(app: FastAPI, settings: Settings, db: Any, local: Store, templates: Jinja2Templates,
             moph_fetch: Callable[..., dict] = _http_json) -> None:

    def current_user(request: Request) -> dict | None:
        token = request.cookies.get(COOKIE)
        return local.session(token_hash(token)) if token else None

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        request.state.user = None
        if settings.login_required:
            user = current_user(request)
            public = request.url.path in PUBLIC_PATHS or request.url.path.startswith("/static/fonts/")
            if user is None and not public:
                if request.url.path.startswith("/api/"):
                    return JSONResponse({"detail": "กรุณาเข้าสู่ระบบ", "login": True}, status_code=401)
                had_cookie = COOKIE in request.cookies
                return RedirectResponse("/login" + ("?error=expired" if had_cookie else ""), status_code=303)
            request.state.user = user
        return await call_next(request)

    def start_session(user_id: str, name: str, method: str, target: str = "/") -> RedirectResponse | JSONResponse:
        token = secrets.token_urlsafe(32)
        local.create_session(token_hash(token), user_id, name, method, settings.session_hours)
        local.log_login(user_id, method, True)
        log.info("login %s via %s", user_id, method)
        response: Any = JSONResponse({"ok": True, "next": target}) if method == "hosxp" else RedirectResponse(target, status_code=303)
        response.set_cookie(COOKIE, token, max_age=settings.session_hours * 3600, httponly=True, samesite="lax", path="/",
                            secure=bool(settings.ssl_certfile))
        return response

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, error: str = ""):
        if not settings.login_required or current_user(request):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {
            "moph_ready": settings.moph_ready, "error": LOGIN_ERRORS.get(error, ""), "hname": settings.hname,
        })

    @app.post("/auth/login")
    def login(body: dict = Body(...)):
        username, password = text(body.get("username")), str(body.get("password") or "")
        if not username or not password:
            return JSONResponse({"detail": "กรุณากรอกชื่อผู้ใช้และรหัสผ่าน"}, status_code=400)
        user_id = f"hosxp:{username}"
        if local.recent_failures(user_id) >= MAX_FAILURES:
            return JSONResponse({"detail": "กรอกรหัสผ่านผิดหลายครั้ง กรุณารอ 15 นาทีแล้วลองใหม่"}, status_code=429)
        try:
            account = db.login_user(username)
        except HosxpError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)
        reason = ""
        if account is None or not password_matches(password, account.get("passweb")):
            reason = "wrong"
        elif text(account.get("account_disable")).upper() == "Y":
            reason = "disabled"
        elif settings.login_groups and text(account.get("groupname")) not in settings.login_groups:
            reason = "group"
        else:
            expire = account.get("password_expire_date")
            if isinstance(expire, (date, datetime)) and (expire.date() if isinstance(expire, datetime) else expire) < date.today():
                reason = "expired"
        if reason:
            local.log_login(user_id, "hosxp", False, reason)
            message = {
                "disabled": "บัญชีนี้ถูกปิดใช้งานใน HOSxP",
                "group": "บัญชีนี้ไม่มีสิทธิ์ใช้โปรแกรมนี้",
                "expired": "รหัสผ่านหมดอายุ กรุณาเปลี่ยนรหัสผ่านใน HOSxP",
            }.get(reason, "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
            return JSONResponse({"detail": message}, status_code=401)
        return start_session(user_id, text(account.get("name")) or username, "hosxp")

    @app.get("/auth/moph/start")
    def moph_start(request: Request):
        if not settings.moph_ready:
            return RedirectResponse("/login?error=moph_off", status_code=303)
        # The state cookie must live on the same host MOPH ID redirects back to.
        target = urllib.parse.urlsplit(settings.moph_redirect_uri)
        if target.netloc and target.netloc != request.headers.get("host"):
            return RedirectResponse(f"{target.scheme}://{target.netloc}/auth/moph/start", status_code=303)
        state = secrets.token_urlsafe(24)
        local.save_login_state(token_hash(state))
        response = RedirectResponse(moph_authorize_url(settings, state), status_code=303)
        response.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax", path="/auth",
                            secure=settings.moph_redirect_uri.startswith("https://"))
        return response

    @app.get("/auth/healthid/callback")
    @app.get("/auth/moph/callback")
    def moph_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        def fail(kind: str, detail: str = "") -> RedirectResponse:
            local.log_login("moph:?", "moph", False, detail or kind)
            response = RedirectResponse(f"/login?error={kind}", status_code=303)
            response.delete_cookie(STATE_COOKIE, path="/auth")
            return response

        if error or not code:
            return fail("moph_denied")
        cookie_state = request.cookies.get(STATE_COOKIE, "")
        if not state or not hmac.compare_digest(state, cookie_state) or not local.take_login_state(token_hash(state)):
            return fail("moph_state")
        try:
            profile = moph_profile(settings, code, moph_fetch)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError) as exc:
            where = getattr(exc, "url", "") or ""
            reason = f"{exc.__class__.__name__} {getattr(exc, 'code', '')} {urllib.parse.urlsplit(where).netloc}".strip()
            if getattr(exc, "detail", ""):
                reason += f" | {exc.detail[:300]}"
            if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
                reason = f"ValueError {exc}"
            log.warning("MOPH ID login failed: %s", reason)
            return fail("moph_failed", reason)
        provider_id = text(profile.get("provider_id")) or text(profile.get("account_id"))
        if not allowed_hospital(profile, settings.moph_hcodes):
            local.log_login(f"moph:{provider_id}", "moph", False, "hospital")
            return fail("moph_hospital", "hospital")
        name = text(profile.get("name_th")) or " ".join(
            p for p in (text(profile.get("firstname_th")), text(profile.get("lastname_th"))) if p) or provider_id
        response = start_session(f"moph:{provider_id}", name, "moph")
        response.delete_cookie(STATE_COOKIE, path="/auth")
        return response

    @app.post("/auth/logout")
    def logout(request: Request):
        token = request.cookies.get(COOKIE)
        if token:
            local.delete_session(token_hash(token))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/api/me")
    def me(request: Request):
        user = request.state.user
        return {"login_required": settings.login_required, "name": user["name"] if user else "",
                "method": user["method"] if user else ""}


def user_label(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return f"{user['name']} ({user['user_id']})" if user else ""
