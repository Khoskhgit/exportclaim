import time
import urllib.parse
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import eclaim_web
from app.eclaim_web import BASE, LIST_URL, EclaimWeb, EclaimWebError, RepLink
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp
from test_repstm import ofc_rep_xls

REP_XLS = ofc_rep_xls([("691100065", 1, "08/11/2026 12:02:00", 65.0, "-", 65.0, "691108120216")])

# RFC 6238 appendix B: ASCII "12345678901234567890" (base32 below), SHA-1, the 8-digit codes end in these 6 digits
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_totp_matches_rfc6238():
    assert eclaim_web.totp(SECRET, 59) == "287082"
    assert eclaim_web.totp(SECRET, 1111111109) == "081804"
    assert eclaim_web.totp(SECRET.lower()[:16] + " " + SECRET.lower()[16:], 1234567890) == "005924"


LIST_HTML = """<form id="form-search" name="searchH" method="POST" action="?"><input type="hidden" name="zp" value="10866"/></form>
<table id="content2"><thead><tr><th>วันที่นำส่งข้อมูล</th><th>ลำดับที่</th><th>File</th><th>จำนวน</th>
<th>Rep File</th><th>Excel File</th></tr></thead><tbody>
<tr class="color4"><td>19/11/2569 22:50</td><td>691100065</td><td>eclaim_10866_OP_25691119_225004987.ecd</td>
<td><a href="/webComponent/validation/ValidationMainAction.do?type=A">13</a></td>
<td><a href="/webComponent/validation/GetFileAction.do?fn=x.ecd">rep</a></td>
<td><a href="/webComponent/invoice/InvoiceReportExcelAction.do?status=excel&amp;filename=eclaim_10866_OP_25691119_225004987.ecd">download excel</a></td></tr>
<tr class="color4"><td>18/11/2569</td><td>691100064</td><td>eclaim_10866_OPCS_25691118_1.ecd</td><td>1</td><td></td><td></td></tr>
</tbody></table>"""


def test_rep_links_read_the_list_page():
    links = eclaim_web.rep_links(LIST_HTML, "ucs", LIST_URL)
    assert links == [RepLink("691100065", "ucs", "eclaim_10866_OP_25691119_225004987.xls",
                             "https://eclaim.nhso.go.th/webComponent/invoice/InvoiceReportExcelAction.do"
                             "?status=excel&filename=eclaim_10866_OP_25691119_225004987.ecd")]
    with pytest.raises(EclaimWebError, match="session"):
        eclaim_web.rep_links("<html>login</html>", "ucs")


def test_months_back_cross_the_year():
    assert eclaim_web.months_back(3, time.strptime("2027-02-10", "%Y-%m-%d")) == [(2, 2570), (1, 2570), (12, 2569)]


class Response:
    def __init__(self, url, body):
        self.url, self.body = url, body.encode() if isinstance(body, str) else body
        self.headers = self

    def get_content_charset(self):
        return "utf-8"

    def geturl(self):
        return self.url

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


IAM = "https://iam.nhso.go.th/realms/nhso/login-actions/authenticate?session_code=s"
LOGIN_HTML = f'<form id="kc-form-login" action="{IAM}" method="post"><input name="username"><input name="password" type="password">' \
             '<input type="hidden" name="credentialId" value=""><input type="submit" name="login" value="x"></form>'
OTP_HTML = f'<form id="kc-otp-login-form" action="{IAM}&amp;step=otp" method="post"><input id="otp" name="otp" value="">' \
           '<input type="submit" name="login" value="x"></form>'


class FakeNhso:
    """iam.nhso.go.th + eclaim.nhso.go.th as seen by urllib."""

    def __init__(self, secret, otp_fails=0):
        self.secret, self.otp_fails, self.posts, self.addheaders = secret, otp_fails, [], []
        self.session = False

    def open(self, request, timeout):
        url = request.full_url
        data = dict(urllib.parse.parse_qsl(request.data.decode(), keep_blank_values=True)) if request.data else None
        self.posts.append((url, data))
        if url == eclaim_web.LOGIN_URL:
            return Response(IAM, LOGIN_HTML)
        if url == IAM:
            ok = data == {"username": "u", "password": "p", "credentialId": ""}
            return Response(IAM, OTP_HTML if ok else '<span id="input-error">Invalid username or password.</span>')
        if url.endswith("step=otp"):
            if self.otp_fails:
                self.otp_fails -= 1
                return Response(IAM, '<span id="input-error-otp-code">Invalid authenticator code.</span>' + OTP_HTML)
            assert data["otp"] == eclaim_web.totp(self.secret, 1000)
            self.session = True
            return Response(BASE + "main/MainWebAction.do", "<html>e-Claim</html>")
        if url == LIST_URL:
            if not self.session:
                return Response(IAM, LOGIN_HTML)
            return Response(LIST_URL, LIST_HTML if data["maininscl"] == "ucs" and data["mo"] == "11" else
                            '<form id="form-search"></form>')
        if "InvoiceReportExcelAction" in url:
            return Response(url, REP_XLS)
        raise AssertionError(url)


def client(nhso, user="u"):
    waits = []
    web = EclaimWeb(user, "p", SECRET, "10866", opener=nhso, clock=lambda: 1000.0, sleep=waits.append)
    web.waits = waits
    return web


def test_login_posts_password_then_totp():
    nhso = FakeNhso(SECRET)
    web = client(nhso)
    web.login()
    assert web.logged_in and [u for u, _ in nhso.posts] == [eclaim_web.LOGIN_URL, IAM, IAM + "&step=otp"]
    assert nhso.addheaders == [("User-Agent", "KHH-ClaimSure/1.0")]


def test_login_retries_a_refused_code_once_in_the_next_step():
    web = client(FakeNhso(SECRET, otp_fails=1))
    web.login()
    assert web.logged_in and web.waits == [21.0]
    with pytest.raises(EclaimWebError, match="TOTP"):
        client(FakeNhso(SECRET, otp_fails=2)).login()


def test_wrong_password_is_reported():
    with pytest.raises(EclaimWebError, match="Invalid username"):
        client(FakeNhso(SECRET), user="x").login()
    with pytest.raises(EclaimWebError, match="ECLAIM_TOTP_SECRET"):
        EclaimWeb("u", "p", "", "10866", opener=FakeNhso(SECRET)).login()


def wait_job(app):
    for _ in range(200):
        job = app.get("/api/repstm/eclaim").json()["job"]
        if not job["running"]:
            return job
        time.sleep(0.02)
    raise AssertionError("the pull did not finish")


def test_pull_imports_only_new_reps_in_the_background(tmp_path, monkeypatch):
    monkeypatch.setattr(eclaim_web, "months_back", lambda n: [(11, 2569), (10, 2569)][:n])
    nhso = FakeNhso(SECRET)
    config = replace(settings(), export_dir=tmp_path / "exports", eclaim_user="u", eclaim_password="p", eclaim_totp_secret=SECRET)
    app = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3"), eclaim=client(nhso)))
    status = app.get("/api/repstm/eclaim").json()
    assert (status["ready"], status["user"], status["job"]["running"]) == (True, "u", False)

    started = app.post("/api/repstm/eclaim", json={"months": 2, "funds": ["ucs", "ofc", "xxx"]}).json()["job"]
    assert started["funds"] == ["ucs", "ofc"] and started["months"] == 2
    job = wait_job(app)
    assert (job["found"], job["known"], job["total"], job["done"], job["imported"], job["errors"]) == (1, 0, 1, 1, 1, 0), job["lines"]
    rep = [line for line in job["lines"] if line["kind"] == "rep"]
    assert rep == [{"kind": "rep", "ok": True, "rep_no": "691100065", "fund": "ucs", "name": "eclaim_10866_OP_25691119_225004987.xls",
                    "sent": 1, "failed": 0, "text": "REP 691100065 (1 รายการ, ไม่ผ่าน 0)"}]
    assert job["phase"] == "เสร็จ" and job["finished_at"]
    assert [p["maininscl"] for u, p in nhso.posts if u == LIST_URL] == ["ucs", "ucs", "ofc", "ofc"]
    assert app.get("/api/repstm/list").json()["reps"][0]["rep_no"] == "691100065"

    app.post("/api/repstm/eclaim", json={"months": 1, "funds": ["ucs"]})
    again = wait_job(app)
    assert (again["found"], again["known"], again["total"], again["imported"]) == (1, 1, 0, 0)
    assert sum(u == eclaim_web.LOGIN_URL for u, _ in nhso.posts) == 1  # the session is kept between pulls


def test_a_second_pull_waits_for_the_first(tmp_path):
    import threading
    gate = threading.Event()

    class Slow:
        def list_reps(self, *a):
            gate.wait(5)
            return []

    config = replace(settings(), export_dir=tmp_path / "exports", eclaim_user="u", eclaim_password="p", eclaim_totp_secret=SECRET)
    app = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3"), eclaim=Slow()))
    assert app.post("/api/repstm/eclaim", json={"months": 1, "funds": ["ucs"]}).status_code == 200
    busy = app.post("/api/repstm/eclaim", json={"months": 1, "funds": ["ucs"]})
    assert busy.status_code == 409 and "กำลังดึง REP อยู่" in busy.json()["detail"]
    gate.set()
    assert wait_job(app)["running"] is False


def test_pull_needs_settings(tmp_path):
    config = replace(settings(), export_dir=tmp_path / "exports")
    app = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3")))
    assert app.get("/api/repstm/eclaim").json()["ready"] is False
    app.post("/api/repstm/eclaim", json={"months": 1, "funds": ["ucs"]})
    job = wait_job(app)
    assert job["errors"] == 1 and "ECLAIM_TOTP_SECRET" in job["lines"][-1]["text"]
