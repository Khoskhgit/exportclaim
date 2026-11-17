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


def test_a_drug_before_the_window_still_counts_as_a_drug():
    d = {**data([hba1c("v1", "2025-01-01", "6.0"), hba1c("v2", "2025-05-01", "6.1")]),
         "last_drug": {"1": (date(2019, 3, 1), "METFORMIN 500 mg")}}
    (row,) = run(d).values()
    assert codes(row) == ([], []) and row.extra["last_drug"] == "2019-03-01"


def test_fetch_reads_the_range_once_and_the_drugs_by_icode():
    from app.ppfs_dmrem import fetch
    seen = []

    def q(sql):
        seen.append(sql)
        if "lab_order" in sql:
            return [hba1c("v0", "2024-12-01", "6.3"), hba1c("v1", "2025-11-01", "6.0"), hba1c("x1", "2025-02-01", "6.0", hn="2")]
        if "FROM drugitems" in sql:
            return [{"icode": "1500198", "name": "METFORMIN 500 mg"}]
        if "MAX(vstdate)" in sql:
            return [{"hn": "1", "d": date(2020, 1, 1), "icode": "1500198"}]
        return []

    got = fetch(q, date(2025, 10, 1), date(2026, 9, 30))
    assert [t["vn"] for t in got["tests"]] == ["v0", "v1"]  # hn 2 had no test in the range
    assert got["last_drug"] == {"1": (date(2020, 1, 1), "METFORMIN 500 mg")}
    assert "2024-10-01" in seen[0] and "REGEXP" not in seen[2]  # a year back; drugs by icode, not a name match per item
    assert "qty>0" in seen[2] and "qty>0" in seen[4]  # a drug keyed with qty 0 means it was stopped
