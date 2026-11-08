import hashlib
from dataclasses import replace
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp

MD5 = hashlib.md5(b"secret1").hexdigest().upper()


class LoginHosxp(FakeHosxp):
    accounts = {
        "nurse": {"loginname": "nurse", "name": "พยาบาล ทดสอบ", "passweb": MD5, "groupname": "IPD", "account_disable": "N", "password_expire_date": None},
        "off": {"loginname": "off", "name": "ปิดแล้ว", "passweb": MD5, "groupname": "IPD", "account_disable": "Y", "password_expire_date": None},
        "old": {"loginname": "old", "name": "หมดอายุ", "passweb": MD5, "groupname": "IPD", "account_disable": "N", "password_expire_date": date(2020, 1, 1)},
        "lab": {"loginname": "lab", "name": "แล็บ", "passweb": MD5, "groupname": "LAB", "account_disable": "N", "password_expire_date": None},
    }

    def login_user(self, name):
        return self.accounts.get(name)


PROFILE = {"provider_id": "P123", "name_th": "นพ.ทดสอบ ระบบ", "organization": [{"hcode": "11111"}]}


def fake_moph(profile):
    calls = []

    def fetch(url, **kw):
        calls.append(url)
        if url == auth.MOPH_TOKEN:
            assert kw["form"]["grant_type"] == "authorization_code" and kw["form"]["code"] == "CODE"
            return {"data": {"access_token": "H"}}
        if url == auth.PROVIDER_TOKEN:
            assert kw["body"]["token"] == "H" and kw["body"]["token_by"] == "Health ID"
            return {"data": {"access_token": "P"}}
        assert kw["headers"]["Authorization"] == "Bearer P"
        return {"data": profile}
    fetch.calls = calls
    return fetch


def make(tmp_path, profile=PROFILE, **overrides):
    values = dict(export_dir=tmp_path / "exports", login_required=True, login_groups=("IPD",),
                  moph_health_id_client="h", moph_health_id_secret="hs", moph_provider_id_client="p",
                  moph_provider_id_secret="ps", moph_redirect_uri="http://testserver/auth/moph/callback")
    config = replace(settings(), **{**values, **overrides})
    return TestClient(create_app(config, LoginHosxp(), Store(tmp_path / "s.sqlite3"), moph_fetch=fake_moph(profile)))


def login(client, user="nurse", password="secret1"):
    return client.post("/auth/login", json={"username": user, "password": password})


def test_password_hash():
    assert auth.password_matches("secret1", MD5) and auth.password_matches("secret1", MD5.lower())
    assert auth.password_matches("รหัส", hashlib.md5("รหัส".encode("cp874")).hexdigest())
    assert not auth.password_matches("secret2", MD5) and not auth.password_matches("x", "")


def test_pages_need_login(tmp_path):
    client = make(tmp_path)
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login"
    assert client.get("/aipn", follow_redirects=False).status_code == 303
    api = client.get("/api/status")
    assert api.status_code == 401 and api.json()["login"]
    assert client.post("/api/aipn/search", json={}).status_code == 401
    assert client.get("/login").status_code == 200
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js", follow_redirects=False).status_code == 303


def test_hosxp_login_and_logout(tmp_path):
    client = make(tmp_path)
    r = login(client)
    assert r.status_code == 200 and r.json()["next"] == "/"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert client.get("/api/me").json()["name"] == "พยาบาล ทดสอบ"
    assert client.get("/").status_code == 200
    assert client.get("/login", follow_redirects=False).status_code == 303
    client.post("/auth/logout")
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize("user,password,message", [
    ("nurse", "wrong", "ไม่ถูกต้อง"), ("ghost", "secret1", "ไม่ถูกต้อง"), ("off", "secret1", "ปิดใช้งาน"),
    ("old", "secret1", "หมดอายุ"), ("lab", "secret1", "ไม่มีสิทธิ์"),
])
def test_hosxp_login_refused(tmp_path, user, password, message):
    r = login(make(tmp_path), user, password)
    assert r.status_code == 401 and message in r.json()["detail"]


def test_too_many_failures_lock_the_account(tmp_path):
    client = make(tmp_path)
    for _ in range(auth.MAX_FAILURES):
        assert login(client, password="bad").status_code == 401
    assert login(client).status_code == 429


def test_session_is_stored_hashed(tmp_path):
    client = make(tmp_path)
    token = login(client).cookies[auth.COOKIE]
    store = Store(tmp_path / "s.sqlite3")
    assert store.session(token) is None and store.session(auth.token_hash(token))["user_id"] == "hosxp:nurse"


def moph_start(client):
    r = client.get("/auth/moph/start", follow_redirects=False)
    assert r.status_code == 303
    url = urlsplit(r.headers["location"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == auth.MOPH_AUTHORIZE
    assert query["client_id"] == ["h"] and query["redirect_uri"] == ["http://testserver/auth/moph/callback"]
    return query["state"][0]


def test_moph_login(tmp_path):
    client = make(tmp_path)
    state = moph_start(client)
    r = client.get(f"/auth/moph/callback?code=CODE&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    me = client.get("/api/me").json()
    assert me["name"] == "นพ.ทดสอบ ระบบ" and me["method"] == "moph"
    # A state works once.
    again = client.get(f"/auth/moph/callback?code=CODE&state={state}", follow_redirects=False)
    assert again.headers["location"] == "/login?error=moph_state"


def test_moph_rejects_bad_state_and_other_hospital(tmp_path):
    client = make(tmp_path)
    moph_start(client)
    r = client.get("/auth/moph/callback?code=CODE&state=forged", follow_redirects=False)
    assert r.headers["location"] == "/login?error=moph_state"
    other = make(tmp_path / "b", profile={**PROFILE, "organization": [{"hcode": "99999"}]})
    state = moph_start(other)
    r = other.get(f"/auth/moph/callback?code=CODE&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/login?error=moph_hospital"
    assert other.get("/api/me").status_code == 401


def test_moph_off_and_host_redirect(tmp_path):
    off = make(tmp_path, moph_health_id_client="")
    assert off.get("/auth/moph/start", follow_redirects=False).headers["location"] == "/login?error=moph_off"
    assert "ยังไม่ได้ตั้งค่า MOPH ID" in off.get("/login").text
    elsewhere = make(tmp_path / "c", moph_redirect_uri="http://localhost:5180/auth/moph/callback")
    r = elsewhere.get("/auth/moph/start", follow_redirects=False)
    assert r.headers["location"] == "http://localhost:5180/auth/moph/start"


def test_export_records_who_made_it(tmp_path):
    client = make(tmp_path)
    login(client)
    from test_api import OK
    client.post("/api/search", json={"start": "2026-10-01", "end": "2026-10-31", "pttypes": ["S1"]})
    r = client.post("/api/export", json={"vns": [OK], "session": "1", "tflag": "A", "file_date": "2026-10-31"})
    assert r.status_code == 200
    assert client.get("/api/exports").json()[0]["created_by"] == "พยาบาล ทดสอบ (hosxp:nurse)"


def test_healthid_callback_path_and_hospital_code(tmp_path):
    client = make(tmp_path, moph_redirect_uri="http://testserver/auth/healthid/callback", moph_allowed_hcodes=("10866",))
    r = client.get("/auth/moph/start", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = client.get(f"/auth/healthid/callback?code=CODE&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/login?error=moph_hospital"  # PROFILE belongs to 11111, not 10866
    ok = make(tmp_path / "ok", profile={**PROFILE, "organization": [{"hcode": "99999"}, {"hcode": "10866"}]},
              moph_redirect_uri="http://testserver/auth/healthid/callback", moph_allowed_hcodes=("10866",))
    r = ok.get("/auth/moph/start", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    assert ok.get(f"/auth/healthid/callback?code=CODE&state={state}", follow_redirects=False).headers["location"] == "/"


def test_key_sheet_names_are_read(tmp_path, monkeypatch):
    from app.config import load_settings
    env = tmp_path / ".env"
    env.write_text(
        "HCODE=11111\nHEALTHID_CLIENT_ID=hid\nHEALTHID_CLIENT_SECRET=hsec\nHEALTHID_BASE_URL=https://moph.id.th/\n"
        "HEALTHID_REDIRECT_URI=https://localhost:5180/auth/healthid/callback\nPROVIDERID_CLIENT_ID=pid\n"
        "PROVIDERID_CLIENT_SECRET=psec\nPROVIDERID_API_URL=https://provider.id.th\nHOSPITAL_CODE=10866\n", encoding="utf-8")
    for name in ("HCODE", "HEALTHID_CLIENT_ID", "HEALTHID_CLIENT_SECRET", "HEALTHID_BASE_URL", "HEALTHID_REDIRECT_URI",
                 "PROVIDERID_CLIENT_ID", "PROVIDERID_CLIENT_SECRET", "PROVIDERID_API_URL", "HOSPITAL_CODE",
                 "MOPH_HEALTH_ID_CLIENT", "MOPH_HEALTH_ID_SECRET", "MOPH_PROVIDER_ID_CLIENT", "MOPH_PROVIDER_ID_SECRET",
                 "MOPH_REDIRECT_URI", "MOPH_ALLOWED_HCODES"):
        monkeypatch.delenv(name, raising=False)
    s = load_settings(env)
    assert s.moph_ready and s.moph_hcodes == ("10866",)
    assert s.moph_redirect_uri == "https://localhost:5180/auth/healthid/callback"
    assert auth.moph_authorize_url(s, "X").startswith("https://moph.id.th/oauth/redirect?client_id=hid")


def test_moph_requests_name_the_program(monkeypatch):
    seen = {}

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"data": {}}'

    def fake_open(request, timeout):
        seen["ua"] = request.get_header("User-agent")
        return Reply()
    monkeypatch.setattr(auth.urllib.request, "urlopen", fake_open)
    auth._http_json("https://provider.id.th/x", body={})
    assert seen["ua"] == "EMR"
