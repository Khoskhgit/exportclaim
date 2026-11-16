from datetime import date
from decimal import Decimal

from app.ppfs_dmrem import check, stage_visits, value


def hba1c(vn, day, result, hn="1", age=55, maininscl="UCS", an=None):
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": an, "pttype": "89",
            "pttype_name": "บัตรทอง", "maininscl": maininscl, "ptname": "ผู้ป่วย ทดสอบ", "cid": "1" * 13, "nationality": "99",
            "sex": "1", "age_y": age, "result": result}


def drug(day, hn="1"):
    return {"hn": hn, "vstdate": date.fromisoformat(day), "name": "METFORMIN 500 mg"}


def data(tests, drugs=(), e11=("1",)):
    return {"tests": list(tests), "drugs": list(drugs), "e11": set(e11)}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def run(d, start="2025-01-01", end="2026-12-31"):
    return {r.code: r for r in check(d, date.fromisoformat(start), date.fromisoformat(end))}


def test_reading_the_result():
    assert [value(v) for v in ("6.2", " 5.9 %", "<4.0", "")] == [Decimal("6.2"), Decimal("5.9"), None, None]


def test_both_stages_after_stopping_the_drug():
    rows = run(data([hba1c("v1", "2025-06-01", "7.1"), hba1c("v2", "2025-09-01", "6.1"), hba1c("v3", "2025-12-15", "6.0"),
                     hba1c("v4", "2026-04-01", "5.9")], drugs=[drug("2025-05-20")]))
    one, two = rows["DMR1"], rows["DMR2"]
    assert (one.vn, one.amount, codes(one)) == ("v3", Decimal("4000"), ([], []))
    assert [t["vn"] for t in one.extra["tests"]] == ["v2", "v3"]
    assert (two.vn, two.amount, codes(two)) == ("v4", Decimal("2000"), ([], []))


def test_not_yet_in_remission():
    # Too close together, then a drug within 3 months before the first test, then a high result in between.
    assert run(data([hba1c("v1", "2025-09-01", "6.0"), hba1c("v2", "2025-10-15", "6.1")])) == {}
    assert run(data([hba1c("v1", "2025-09-01", "6.0"), hba1c("v2", "2025-12-15", "6.1")], drugs=[drug("2025-07-01")])) == {}
    assert run(data([hba1c("v1", "2025-01-01", "6.0"), hba1c("v2", "2025-04-15", "6.8"), hba1c("v3", "2025-08-01", "6.1")])) == {}


def test_who_may_be_claimed():
    rows = run(data([hba1c("v1", "2025-01-01", "6.0", maininscl="SSS", age=17), hba1c("v2", "2025-05-01", "6.1", maininscl="SSS", age=17)],
                    e11=()))
    (row,) = rows.values()
    assert codes(row) == (["NOT_UC", "NO_E11", "AGE"], ["NEVER_DRUG"]) and row.code == "DMR1"


def test_only_rows_whose_visit_is_in_the_range_and_the_visits_sent():
    d = data([hba1c("v1", "2025-01-01", "6.0"), hba1c("v2", "2025-05-01", "6.1"), hba1c("v3", "2025-09-01", "6.2")], drugs=[drug("2024-01-01")])
    rows = run(d, start="2025-08-01", end="2025-12-31")
    assert list(rows) == ["DMR2"]  # stage 1 (v2) is before the range
    assert stage_visits(list(rows.values())) == ["v1", "v2", "v3"]  # every HbA1c of the stage goes into the ZIP
