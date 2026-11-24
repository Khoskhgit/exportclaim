import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import aipn_web
from app.aipn_web import AipnFile, AipnWebError
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp
from test_repstm import aipn_zips

REP_PAGE = """<form action="/aipnupload/SearchRep"><input name="__RequestVerificationToken" type="hidden" value="t1" /></form>
<table id="tableRep" class="table"><thead><tr><th>ลำดับ</th></tr></thead><tbody>
<tr class="unprotected"><td class="protected text-center">1</td>
  <td class="text-center">10118</td>
  <td class="text-start" data-order="20261120143554">20 พฤศจิกายน 2569</td><td class="protected"><div id="repDownload"></div></td></tr>
<tr class="unprotected"><td class="protected text-center">2</td>
  <td class="text-center">10117</td>
  <td class="text-start" data-order="20261009141323">09 ตุลาคม 2569</td><td class="protected"></td></tr>
</tbody></table><a href="/aipnupload/Operation/Logout">ออกจากระบบ</a>"""


def test_rows_and_csrf():
    assert aipn_web.rows(REP_PAGE, "rep") == [AipnFile("rep", "10118", "2026-11-20T14:35:54"),
                                              AipnFile("rep", "10117", "2026-10-09T14:13:23")]
    assert aipn_web.csrf(REP_PAGE) == "t1"
    with pytest.raises(AipnWebError, match="Statement"):
        aipn_web.rows(REP_PAGE, "stm")
    with pytest.raises(AipnWebError):
        aipn_web.csrf("<form></form>")


def test_login_needs_settings():
    with pytest.raises(AipnWebError, match="AIPN_USER"):
        aipn_web.AipnWeb("11111", "", "").login()


class FakeAipn:
    def __init__(self):
        rep, stm = aipn_zips()
        self.files = {("rep", "10117"): ("11111_SIGNREP_10117.ZIP", rep), ("stm", "6910"): ("11111_SIGNSTM_6910.ZIP", stm),
                      ("rep", "10001"): ("x", b"")}
        self.downloads = []

    def list_files(self, kind):
        today = time.strftime("%Y-%m-%dT08:00:00")
        old = "2020-01-01T08:00:00"
        return {"rep": [AipnFile("rep", "10117", today), AipnFile("rep", "10001", old)],
                "stm": [AipnFile("stm", "6910", today)]}[kind]

    def download(self, item):
        self.downloads.append((item.kind, item.no))
        return self.files[(item.kind, item.no)]


def wait(client):
    for _ in range(300):
        status = client.get("/api/repstm/aipn").json()
        if not status["job"]["running"]:
            return status
        time.sleep(0.02)
    raise AssertionError("AIPN pull did not finish")


def test_pull_imports_new_rep_and_stm_once(tmp_path):
    fake = FakeAipn()
    config = replace(settings(), export_dir=tmp_path / "e", aipn_hcode="11111", aipn_user="u", aipn_password="p")
    client = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3"), aipn=fake))
    assert client.get("/api/repstm/aipn").json()["ready"] is True
    assert client.post("/api/repstm/aipn", json={"months": 2}).status_code == 200
    job = wait(client)["job"]
    assert sorted(fake.downloads) == [("rep", "10117"), ("stm", "6910")]  # REP 10001 is older than 2 months
    assert (job["found"], job["known"], job["total"], job["imported"], job["errors"]) == (2, 0, 2, 2, 0)
    listing = client.get("/api/repstm/list").json()
    assert "10117" in {r["rep_no"] for r in listing["reps"]} and "11111ST6910" in {s["stm_no"] for s in listing["stms"]}

    client.post("/api/repstm/aipn", json={"months": 2})
    again = wait(client)["job"]
    assert (again["found"], again["known"], again["total"]) == (2, 2, 0) and len(fake.downloads) == 2
    assert client.post("/api/repstm/aipn", json={"months": "x"}).status_code == 400


def test_pull_reports_missing_settings(tmp_path):
    config = replace(settings(), export_dir=tmp_path / "e")
    client = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3")))
    assert client.get("/api/repstm/aipn").json()["ready"] is False
    client.post("/api/repstm/aipn", json={})
    job = wait(client)["job"]
    assert job["errors"] == 1 and "AIPN_USER" in job["lines"][-1]["text"]
