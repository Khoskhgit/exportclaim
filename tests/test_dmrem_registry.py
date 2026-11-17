import io
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook

from app.dmrem_registry import RegistryError, RegistryStore, iso, parse
from app.ppfs_dmrem import check, cutoff, follow_up
from app.store import Store

HEADER = ["RUN NUMBER", "ปีงบประมาณ", "PID", "ชื่อ-นามสกุล", "สิทธิ", "เขต", "จังหวัด", "หน่วยบริการลงทะเบียน",
          "ชนิดการลงทะเบียน", "วันที่ลงทะเบียน", "วันที่เข้ารับบริการ", "สถานะ", "วัน expire date", "เหตุผล"]


def report(*rows) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(["   รายงาน การลงทะเบียนผู้ป่วย T1DM รายใหม่"])
    sheet.append([])
    sheet.append(HEADER)
    for r in rows:
        sheet.append(r)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def reg(run, pid, day, status="Y", expire=""):
    return [run, "2026", pid, "ชื่อ สกุล", "WEL", "เขต 6", "สระแก้ว", "10866-รพ.คลองหาด", "T2DM", day, day, status, expire, ""]


def test_the_report_is_read_without_names(tmp_path):
    rows = parse("T1DM_report.xlsx", report(reg("2027-1", "1" * 13, "17/04/2026"), reg("2027-2", "2" * 13, "01/05/2569"), ["", "", "abc"]))
    assert [(r.run_no, r.pid, r.reg_date, r.status, r.kind) for r in rows] == [
        ("2027-1", "1" * 13, "2026-04-17", "Y", "T2DM"), ("2027-2", "2" * 13, "2026-05-01", "Y", "T2DM")]
    assert not any("ชื่อ" in str(v) for r in rows for v in vars(r).values())
    store = RegistryStore(Store(tmp_path / "s.sqlite3"))
    assert store.save(rows, "T1DM_report.xlsx")["added"] == 2
    assert store.save(rows, "T1DM_report.xlsx")["updated"] == 2
    assert store.summary()["people"] == 2 and list(store.by_pid(["1" * 13])) == ["1" * 13]


def test_bad_files():
    with pytest.raises(RegistryError):
        parse("x.csv", b"a,b")
    with pytest.raises(RegistryError):
        parse("x.xlsx", report())
    assert iso("31/02/2026") == "" and iso(date(2026, 1, 2)) == "2026-01-02"


def hba1c(vn, day, result, hn="1", cid="1" * 13):
    return {"vn": vn, "hn": hn, "cid": cid, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": None, "pttype": "89",
            "pttype_name": "บัตรทอง", "maininscl": "UCS", "ptname": "ผู้ป่วย", "nationality": "99", "sex": "1", "age_y": 55,
            "result": result}


def test_six_checks_the_registration_and_shows_the_gaps():
    data = {"tests": [hba1c("v1", "2025-11-01", "6.0"), hba1c("v2", "2026-02-15", "6.1"),
                      hba1c("w1", "2025-11-01", "6.0", hn="2", cid="2" * 13), hba1c("w2", "2026-02-15", "6.1", hn="2", cid="2" * 13)],
            "drugs": [{"hn": "1", "vstdate": date(2024, 1, 1), "name": "METFORMIN"}, {"hn": "2", "vstdate": date(2024, 1, 1), "name": "METFORMIN"}],
            "e11": {"1", "2"}}
    registry = {"1" * 13: [{"status": "Y", "reg_date": "2026-03-01", "expire_date": ""}]}
    rows = {r.hn: r for r in check(data, date(2025, 10, 1), date(2026, 9, 30), registry=registry)}
    assert rows["1"].extra["gaps"] == [106] and rows["1"].extra["registered"] == "2026-03-01"
    assert [w["code"] for w in rows["1"].warnings] == ["LATE_REGISTER"]  # registered after the test of the stage
    assert [e["code"] for e in rows["2"].errors] == ["NOT_REGISTERED"]
    unchecked = check(data, date(2025, 10, 1), date(2026, 9, 30), registry=None)
    assert not any(e["code"] == "NOT_REGISTERED" for r in unchecked for e in r.errors)  # no registry imported yet


def d(s):
    return date.fromisoformat(s)


def test_follow_up_states_and_due_windows():
    today = d("2026-11-16")
    stage1 = follow_up([(d("2026-01-10"), Decimal("6.0")), (d("2026-05-01"), Decimal("5.8"))], [], today)
    assert (stage1["state"], stage1["next_from"], stage1["next_to"]) == ("wait3", d("2026-07-30"), d("2026-10-31"))
    one = follow_up([(d("2026-09-01"), Decimal("6.2"))], [], today)
    assert (one["state"], one["next_from"]) == ("wait2", d("2026-11-30"))
    on_drug = follow_up([(d("2026-09-01"), Decimal("6.2"))], [d("2026-10-01")], today)
    assert on_drug["state"] == "not_yet" and on_drug["note"] == "ยังได้ยาลดน้ำตาล"
    done = follow_up([(d("2026-01-10"), Decimal("6.0")), (d("2026-05-01"), Decimal("5.8")), (d("2026-09-01"), Decimal("5.9"))], [], today)
    assert done["state"] == "done"
    relapse = follow_up([(d("2026-01-10"), Decimal("6.0")), (d("2026-05-01"), Decimal("5.8")), (d("2026-08-15"), Decimal("7.0"))], [], today)
    assert relapse["state"] == "relapse"
    old = follow_up([(d("2018-01-01"), Decimal("6.0")), (d("2018-05-01"), Decimal("6.0")), (d("2026-09-01"), Decimal("6.2"))], [],
                    today, since=d("2025-04-04"))
    assert old["state"] == "wait2"  # an old remission does not count
    assert cutoff(d("2026-02-01")) == d("2026-03-31") and cutoff(d("2026-04-01")) == d("2026-09-15") and cutoff(d("2026-10-01")) == d("2027-03-31")


def test_the_registry_service_date_is_the_first_day_off_the_drugs():
    tests = [hba1c("v1", "2026-01-09", "6.1"), hba1c("v2", "2026-04-10", "5.3"), hba1c("v3", "2026-07-24", "5.2")]
    drugs = [{"hn": "1", "vstdate": date(2026, 1, 9), "name": "METFORMIN"}, {"hn": "1", "vstdate": date(2026, 3, 20), "name": "METFORMIN"}]
    data = {"tests": tests, "drugs": drugs, "e11": {"1"}}
    reg = {"status": "Y", "reg_date": "2026-05-01", "expire_date": "", "visit_date": "2026-04-10"}
    rows = check(data, date(2025, 10, 1), date(2026, 9, 30), registry={"1" * 13: [reg]})
    # The drug of 20 March is before the day in the registry, so it does not count; the stage starts on that day.
    assert [(r.code, [t["vn"] for t in r.extra["tests"]], r.extra["stopped"]) for r in rows] == [("DMR1", ["v2", "v3"], "2026-04-10")]
    assert check(data, date(2025, 10, 1), date(2026, 9, 30), registry={"1" * 13: [{**reg, "visit_date": ""}]}) == []  # HOSxP decides
    later = {**data, "drugs": drugs + [{"hn": "1", "vstdate": date(2026, 5, 2), "name": "METFORMIN"}]}
    assert check(later, date(2025, 10, 1), date(2026, 9, 30), registry={"1" * 13: [reg]}) == []  # a drug after it still counts


def test_follow_up_counts_from_the_stop_day():
    series = [(d("2026-01-09"), Decimal("6.1")), (d("2026-04-10"), Decimal("5.3"))]
    f = follow_up(series, [d("2026-03-20")], d("2026-06-01"), stop=d("2026-04-10"))
    assert (f["state"], f["next_from"], f["last_drug"]) == ("wait2", d("2026-07-09"), d("2026-03-20"))
    assert follow_up(series, [d("2026-03-20")], d("2026-06-01"))["state"] == "not_yet"
