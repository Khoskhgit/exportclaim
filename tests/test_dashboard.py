from fastapi.testclient import TestClient

from app import dashboard
from app.main import create_app
from app.store import Store
from factories import settings
from test_api import FakeHosxp


def line(invno, stat, codes="", claim="100", paid="", at="2026-11-10T09:00", rep="1", no=1, account="UCS"):
    return {"account": account, "invno": invno, "stat": stat, "codes": codes, "claimamt": claim, "paid": paid,
            "issued_at": at, "rep_no": rep, "line": no}


def test_rep_summary_takes_the_newest_line_per_visit_and_skips_refused_resends():
    lines = [line("v1", "C", "804", at="2026-11-01"), line("v1", "A", paid="90.00", at="2026-11-05", rep="2"),
             line("v1", "C", "R01", at="2026-11-09", rep="3"),  # resend refused as a duplicate: v1 stays A
             line("v2", "C", "DENY", paid="10.00"), line("v3", "C", "804"),
             line("a1", "C", "432", account="SIGN")]
    reps = dashboard.rep_summary(lines, {("UCS", "v1"): 80.0})
    assert [a["account"] for a in reps] == ["UCS", "SIGN"]
    ucs = reps[0]
    assert (ucs["sent"]["n"], ucs["passed"]["n"], ucs["c"]["n"], ucs["deny"]["n"]) == (3, 1, 1, 1)
    assert (ucs["paid"], ucs["stm_paid"], ucs["has_paid"]) == (100.0, 80.0, True)
    assert {c["code"] for c in ucs["codes"]} == {"804", "DENY"}
    dashboard.code_meanings(reps, {"432": "ชื่อจาก REP"})
    assert next(c for c in ucs["codes"] if c["code"] == "804")["name"] and reps[1]["codes"][0]["name"] == "ชื่อจาก REP"


def test_months_fill_the_period_and_keep_the_last_24():
    from datetime import date
    rows = dashboard.fill_months(date(2026, 9, 15), date(2026, 11, 2), [{"month": "2026-10", "items": 5}])
    assert rows == [{"month": "2026-09", "items": 0}, {"month": "2026-10", "items": 5}, {"month": "2026-11", "items": 0}]
    assert len(dashboard.fill_months(date(2020, 1, 1), date(2026, 11, 1), [])) == 24


def test_menu_shows_the_sso_group(tmp_path):
    client = TestClient(create_app(settings(), hosxp=FakeHosxp(), store=Store(tmp_path / "s.sqlite3")))
    page = client.get("/aipn").text
    group = page[page.index('id="sso-menu"'):page.index("</details>", page.index('id="sso-menu"'))]
    assert 'menu-group is-current" id="sso-menu"' in page and all(f'href="/{p}"' in group for p in ("ssop", "aipn", "dental"))
    assert page.count('href="/dental"') == 1
    home = client.get("/").text
    assert 'menu-group" id="sso-menu"' in home and 'menu-group is-current" id="home-menu"' in home


def test_breadcrumb_names_group_and_page(tmp_path):
    client = TestClient(create_app(settings(), hosxp=FakeHosxp(), store=Store(tmp_path / "s.sqlite3")))
    crumbs = client.get("/aipn").text.split('class="crumbs"')[1].split("</ol>")[0]
    assert "<li>ประกันสังคม (SSO)</li>" in crumbs and '<li aria-current="page">AIPN ผู้ป่วยใน</li>' in crumbs
    home = client.get("/").text.split('class="crumbs"')[1].split("</ol>")[0]
    assert '<li aria-current="page">Dashboard</li>' in home and home.count("<li") == 1


def test_home_is_the_dashboard_and_ssop_moved(tmp_path):
    client = TestClient(create_app(settings(), hosxp=FakeHosxp(), store=Store(tmp_path / "s.sqlite3")))
    assert "Dashboard ภาพรวม" in client.get("/").text
    assert "ส่งออก SSOP" in client.get("/ssop").text
    data = client.get("/api/dashboard?start=2025-10-01&end=2026-09-30").json()
    assert data["reps"] == [] and {m["key"] for m in data["menus"]} >= {"ssop", "aipn", "ppfs", "ntip", "dental", "disabled", "approve"}
    assert all(m["items"] == 0 for m in data["menus"])
    months = data["menus"][0]["months"]
    assert [x["month"] for x in months][:2] == ["2025-10", "2025-11"] and len(months) == 12 and all(x["items"] == 0 for x in months)
    assert client.get("/api/dashboard?start=2026-10-01&end=2026-01-01").status_code == 400


def test_rep_months_count_each_visit_once_by_its_newest_result():
    from datetime import date

    from app.dashboard import rep_months
    line = lambda invno, stat, codes, issued, day, no=1: {"account": "UCS", "invno": invno, "stat": stat, "codes": codes,
                                                          "issued_at": issued, "rep_no": f"R{issued}", "line": no, "day": day}
    lines = [line("1", "C", "C438", "2026-10-02", "2026-10-01"), line("1", "A", "", "2026-10-20", "2026-10-01"),
             line("2", "C", "DENY", "2026-11-03", "2026-11-01"), line("3", "C", "C438", "2026-11-03", "2026-11-02")]
    months = rep_months(lines, date(2026, 10, 1), date(2026, 12, 5))
    assert months == [{"month": "2026-10", "passed": 1, "c": 0, "deny": 0},
                      {"month": "2026-11", "passed": 0, "c": 1, "deny": 1},
                      {"month": "2026-12", "passed": 0, "c": 0, "deny": 0}]


def test_approve_tile_counts_payments_by_transaction_date(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    client = TestClient(create_app(settings(), hosxp=FakeHosxp(), store=store))
    with store._db() as db:
        db.executemany("INSERT INTO approve_codes (appr_code, trans_ref, kind, cid, txn_date, amount, updated_at)"
                       " VALUES (?,?,?,?,?,?,?)", [
                           ("1", "a", "PAYMENT", "1" * 13, "2026-10-05", "500.00", "2026-10-06T08:00:00"),
                           ("2", "b", "PAYMENT", "1" * 13, "2026-11-02", "250.00", "2026-11-03T08:00:00"),
                           ("3", "c", "REFUND", "1" * 13, "2026-11-02", "-250.00", "2026-11-03T08:00:00"),
                           ("4", "d", "PAYMENT", "1" * 13, "2026-09-30", "999.00", "2026-10-01T08:00:00")])
    data = client.get("/api/dashboard?start=2026-10-01&end=2026-11-30").json()
    tile = next(m for m in data["menus"] if m["key"] == "approve")
    assert (tile["items"], tile["amount"], tile["href"]) == (2, 750.0, "/approve")
    assert [m["items"] for m in tile["months"]] == [1, 1]


def test_visit_counts_by_fund(tmp_path):
    class Visits(FakeHosxp):
        def visit_counts(self, start, end):
            self.asked = (start, end)
            return {"opd": [{"hipdata": "UCS", "n": 10}, {"hipdata": "WEL", "n": 2}, {"hipdata": "SSS", "n": 5},
                            {"hipdata": "", "n": 1}, {"hipdata": "OFC", "n": 3}],
                    "ipd": [{"hipdata": "UCS", "n": 4}]}

    db = Visits()
    client = TestClient(create_app(settings(), db, Store(tmp_path / "s.sqlite3")))
    data = client.get("/api/dashboard/visits?start=2026-11-01&end=2026-11-26").json()
    assert db.asked == ("2026-11-01", "2026-11-26")
    assert data["opd"] == {"total": 21, "funds": [{"fund": "UCS", "n": 12}, {"fund": "OFC", "n": 3},
                                                  {"fund": "SSS", "n": 5}, {"fund": "อื่น ๆ", "n": 1}]}
    assert data["ipd"]["total"] == 4

    class Down(FakeHosxp):
        def visit_counts(self, start, end):
            raise OSError("Can't connect")

    down = TestClient(create_app(settings(), Down(), Store(tmp_path / "d.sqlite3")))
    r = down.get("/api/dashboard/visits")
    assert r.status_code == 503 and "อ่านยอด visit จาก HOSxP ไม่ได้" in r.json()["detail"]
    assert down.get("/api/dashboard").status_code == 200  # the rest of the page does not need HOSxP
