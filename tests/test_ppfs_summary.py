from decimal import Decimal

from app import ppfs, ppfs_summary


def row(vn, amount, sent="", errors=(), code="X1"):
    return ppfs.Row(key=f"{vn}:{code}", vn=vn, code=code, amount=Decimal(amount), sent=sent, errors=list(errors))


def test_status_of_a_sent_visit_comes_from_the_rep_after_the_send():
    assert ppfs_summary.rep_status(None, "2026-11-01T10:00") == "waiting"
    rep = {"rep_stat": "A", "rep_codes": "", "rep_at": "2026-11-05T09:00"}
    assert ppfs_summary.rep_status(rep, "2026-11-01T10:00") == "passed"
    assert ppfs_summary.rep_status(rep, "2026-11-06T10:00") == "waiting"  # an older claim's REP
    assert ppfs_summary.rep_status({**rep, "rep_stat": "C", "rep_codes": "804"}, "2026-11-01") == "c"
    assert ppfs_summary.rep_status({**rep, "rep_stat": "C", "rep_codes": "DENY"}, "2026-11-01") == "deny"


def test_summary_counts_items_and_pays_each_visit_once():
    rows = [row("v1", "100", "2026-11-01T10:00"), row("v1", "50", "2026-11-01T10:00", code="X2"),
            row("v2", "70", "2026-11-01T10:00"), row("v3", "30", "2026-11-01T10:00"), row("v4", "20", "2026-11-01T10:00"),
            row("v5", "40"), row("v6", "60", errors=[{"code": "E", "message": "x"}])]
    latest = {"v1": {"rep_stat": "A", "rep_codes": "", "rep_at": "2026-11-05", "rep_paid": "150.00", "stm_paid": "150.00"},
              "v2": {"rep_stat": "C", "rep_codes": "804", "rep_at": "2026-11-05", "rep_paid": "0.00"},
              "v3": {"rep_stat": "C", "rep_codes": "DENY", "rep_at": "2026-11-05", "rep_paid": "10.00"}}
    out = ppfs_summary.summarize(rows, latest, {"v1": Decimal("0.5")})
    assert out["all"] == {"n": 7, "amount": "370.00"} and out["visits"] == 6
    assert out["sent"] == {"n": 5, "amount": "270.00"} and out["passed"] == {"n": 2, "amount": "150.00"}
    assert out["c"]["n"] == 1 and out["deny"]["n"] == 1 and out["waiting"] == {"n": 1, "amount": "20.00"}
    assert out["ready"] == {"n": 1, "amount": "40.00"} and out["error"] == {"n": 1, "amount": "60.00"}
    assert (out["paid"], out["stm_paid"]) == ("85.00", "75.00")  # v1 shared half with another fund
    assert [c["code"] for c in out["codes"]] == ["804", "DENY"] and out["codes"][0]["detail"]


def test_shares_split_a_visit_sent_under_two_funds():
    shares = ppfs_summary.shares(["v1", "v2"], {"v1": {"ncd": Decimal(150), "anemia": Decimal(50)}, "v2": {"ncd": Decimal(10)}}, "ncd")
    assert shares == {"v1": Decimal("0.75")}


def test_page_and_date_checks(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.store import Store
    from factories import settings
    from test_api import FakeHosxp
    client = TestClient(create_app(settings(), hosxp=FakeHosxp(), store=Store(tmp_path / "s.sqlite3")))
    assert client.get("/ppfs/summary").status_code == 200
    bad = client.post("/api/ppfs/summary", json={"service": "ncd", "start": "2026-11-30", "end": "2026-11-01"})
    assert bad.status_code == 400
    assert client.post("/api/ppfs/summary", json={"service": "nope", "start": "2026-11-01", "end": "2026-11-30"}).status_code == 400
