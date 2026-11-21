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
    assert "menu-group is-current" in page and all(f'href="/{p}"' in group for p in ("ssop", "aipn", "dental"))
    assert page.count('href="/dental"') == 1
    assert "menu-group is-current" not in client.get("/").text


def test_home_is_the_dashboard_and_ssop_moved(tmp_path):
    client = TestClient(create_app(settings(), hosxp=FakeHosxp(), store=Store(tmp_path / "s.sqlite3")))
    assert "Dashboard ภาพรวม" in client.get("/").text
    assert "ส่งออก SSOP" in client.get("/ssop").text
    data = client.get("/api/dashboard?start=2025-10-01&end=2026-09-30").json()
    assert data["reps"] == [] and {m["key"] for m in data["menus"]} >= {"ssop", "aipn", "ppfs", "ntip", "dental"}
    assert all(m["items"] == 0 for m in data["menus"])
    months = data["menus"][0]["months"]
    assert [x["month"] for x in months][:2] == ["2025-10", "2025-11"] and len(months) == 12 and all(x["items"] == 0 for x in months)
    assert client.get("/api/dashboard?start=2026-10-01&end=2026-01-01").status_code == 400
