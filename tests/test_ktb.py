import time
from dataclasses import replace
from datetime import datetime

from fastapi.testclient import TestClient

from app import ktb_pull
from app.config import load_settings
from app.ktb_web import KtbWebError
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp
from test_approve import A, B, CSV_HEAD, csv_line

CSV = "\r\n".join([CSV_HEAD, csv_line(1, A, "10:40:42", "1,140.50", "Payment", "369817178", "304073552"),
                   csv_line(2, B, "15:01:10", "140.00", "Payment", "369917987", "304144013"),
                   csv_line(3, B, "15:03:31", "140.00", "Void", "369918913", "304144013")]).encode("cp874")
NOW = datetime(2026, 11, 10, 23, 0, tzinfo=ktb_pull.TZ)


class FakeKtb:
    def __init__(self, files=None, error=None):
        self.files, self.error, self.calls = files or [], error, 0

    def online_csvs(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.files


def make(tmp_path, client, sent):
    config = replace(settings(), export_dir=tmp_path / "e", ktb_company_id="c", ktb_user_id="u", ktb_password="p")
    app = create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3"), line_send=sent.append, ktb=lambda: client)
    return app, TestClient(app)


def test_ktb_pull_imports_and_sends_a_summary(tmp_path):
    sent = []
    client = FakeKtb([("healthcare_online_10-11-2026.csv", CSV)])
    app, http = make(tmp_path, client, sent)
    assert http.get("/api/approve/ktb").json()["ready"]
    assert http.post("/api/approve/ktb/run").json() == {"started": True}
    for _ in range(200):
        status = http.get("/api/approve/ktb").json()
        if status["last"] and not status["running"]:
            break
        time.sleep(0.01)
    assert status["last"]["ok"] and client.calls == 1
    rows = http.post("/api/approve/list", json={"start": "2026-11-10", "end": "2026-11-10"}).json()["rows"]
    assert {r["appr_code"] for r in rows} >= {"369817178", "369917987", "369918913"}
    assert sent and "ชำระ 2 รายการ · คืนเงิน/ยกเลิก 1" in sent[-1] and "นำเข้าใหม่ 3 รายการ" in sent[-1]


def test_summary_texts():
    files = [{"files": [{"payments": 2, "refunds": 0, "added": 2, "total": "1280.50"}]}]
    assert ktb_pull.summary(NOW, files).endswith("ชำระ 2 รายการ\nยอดรวม 1,280.50 บาท · นำเข้าใหม่ 2 รายการ")
    assert "10/11/2569 23:00" in ktb_pull.summary(NOW, files)
    assert ktb_pull.summary(NOW, []).endswith("วันนี้ไม่มีรายการรูดบัตร")
    assert ktb_pull.summary(NOW, [], "เข้าสู่ระบบ KTB ไม่ผ่าน").endswith("ดึงไม่สำเร็จ: เข้าสู่ระบบ KTB ไม่ผ่าน")


def test_a_refused_login_is_reported_once(tmp_path):
    sent, client = [], FakeKtb(error=KtbWebError("เข้าสู่ระบบ KTB ไม่ผ่าน"))
    job = ktb_pull.KtbPull(replace(settings(), ktb_company_id="c", ktb_user_id="u", ktb_password="p"),
                           lambda *a: {}, sent.append, client=lambda: client, clock=lambda: NOW)
    last = job.run_once()
    assert client.calls == 1 and not last["ok"] and last["error"] == "เข้าสู่ระบบ KTB ไม่ผ่าน"
    assert sent == [last["text"]]


def test_off_without_keys(tmp_path):
    job = ktb_pull.KtbPull(settings(), lambda *a: {})
    assert not job.ready and job.at is None and job.view()["time"] == ""
    http = TestClient(create_app(replace(settings(), export_dir=tmp_path / "e"), FakeHosxp(), Store(tmp_path / "s.sqlite3")))
    assert http.post("/api/approve/ktb/run").status_code == 400


def test_env_names(tmp_path, monkeypatch):
    for name in ("KTB_COMPANY_ID", "KTB_USER_ID", "KTB_PASSWORD", "KTB_PULL_TIME", "CompanyID", "UserID", "Password"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("CompanyID=HCG1\nUserID=u1\nPassword=p1\n", encoding="utf-8")
    s = load_settings(env)
    assert (s.ktb_company_id, s.ktb_user_id, s.ktb_password, s.ktb_pull_time) == ("HCG1", "u1", "p1", "23:00")
    for name in ("CompanyID", "UserID", "Password"):
        monkeypatch.delenv(name, raising=False)
    env.write_text("KTB_COMPANY_ID=HCG2\nKTB_USER_ID=u2\nKTB_PASSWORD=p2\nKTB_PULL_TIME=22:30\nPassword=other\n", encoding="utf-8")
    s = load_settings(env)
    assert (s.ktb_company_id, s.ktb_user_id, s.ktb_password, s.ktb_pull_time) == ("HCG2", "u2", "p2", "22:30")
    for name in ("KTB_COMPANY_ID", "KTB_USER_ID", "KTB_PASSWORD", "KTB_PULL_TIME", "Password"):
        monkeypatch.delenv(name, raising=False)


def test_summary_goes_to_line_oa_and_moph_alert(monkeypatch):
    got = []
    monkeypatch.setattr(ktb_pull.line_push, "send", lambda token, to, text: got.append(("oa", token, to)))
    def moph(ck, sk, text, url):
        got.append(("moph", ck))
        raise ktb_pull.LineError("MOPH Alert ตอบ HTTP 500")
    monkeypatch.setattr(ktb_pull.moph_alert, "send", moph)
    config = replace(settings(), ktb_company_id="c", ktb_user_id="u", ktb_password="p", line_channel_token="tok",
                     line_to="C" + "1" * 32, moph_alert_client_key="ck", moph_alert_secret_key="sk")
    job = ktb_pull.KtbPull(config, lambda *a: {"files": []}, client=lambda: FakeKtb([("x.csv", b"")]), clock=lambda: NOW)
    last = job.run_once()
    assert got == [("oa", "tok", "C" + "1" * 32), ("moph", "ck")]
    assert last["line"] == "ส่ง LINE OA แล้ว · MOPH Alert: MOPH Alert ตอบ HTTP 500"
    assert [n for n, _ in ktb_pull.senders(settings())] == []
