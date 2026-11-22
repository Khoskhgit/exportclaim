from datetime import date
from decimal import Decimal

from app import ppfs, ppfs_gdm


def visit(vn, day, **kw):
    return {"vn": vn, "hn": kw.get("hn", "1"), "vstdate": day, "vsttime": "09:00:00", "an": kw.get("an"), "pttype": "20",
            "pttype_name": "บัตรทอง", "maininscl": kw.get("inscl", "UCS"), "ptname": "x", "cid": "1" * 13,
            "sex": kw.get("sex", "2"), "age_y": 30, "icd10": "O244"}


def lab(vn, day, result="95", hn="1", name="DTX"):
    return {"hn": hn, "vn": vn, "vstdate": day, "name": name, "result": result}


def test_one_row_per_pregnancy_with_warnings_for_missing_services():
    data = {"visits": [visit("a", "2026-03-01"), visit("b", "2026-04-01"), visit("c", "2027-03-01")],
            "dtx": [lab("b", "2026-04-01")], "ogtt": [], "anc": [{"hn": "1", "labor_date": "2026-08-01"}]}
    rows = ppfs_gdm.check(data, ppfs_gdm.GDM, date(2026, 1, 1), date(2027, 12, 31))
    assert [(r.vn, r.amount, r.labor_date) for r in rows] == [("a", ppfs_gdm.GDM.rate, "2026-08-01"), ("c", ppfs_gdm.GDM.rate, "")]
    first, second = rows
    assert not first.errors and [w["code"] for w in first.warnings] == ["NO_OGTT"] and "คลอด 1/8/2569" in first.warnings[0]["message"]
    assert [w["code"] for w in second.warnings] == ["NO_DTX", "NO_OGTT"]


def test_ogtt_after_the_birth_clears_the_warning_and_a_sent_visit_stands_for_its_pregnancy():
    data = {"visits": [visit("a", "2026-03-01"), visit("b", "2026-04-01")], "dtx": [lab("a", "2026-03-01")],
            "ogtt": [lab("d", "2026-09-15", "120", name="glucose by 2 hr 75g")], "anc": [{"hn": "1", "labor_date": "2026-08-01"}]}
    rows = ppfs_gdm.check(data, ppfs_gdm.GDM, date(2026, 1, 1), date(2026, 12, 31), {("b", "GDM"): "2026-05-01T10:00:00"})
    assert [(r.vn, r.sent, r.warnings) for r in rows] == [("b", "2026-05-01T10:00:00", [])]
    # the first visit is before the searched range: the case is not shown again
    assert ppfs_gdm.check(data, ppfs_gdm.GDM, date(2026, 3, 15), date(2026, 3, 31)) == []


def test_other_rights_men_and_admissions_are_errors():
    data = {"visits": [visit("a", "2026-03-01", inscl="SSS"), visit("b", "2026-03-02", hn="2", sex="1"),
                       visit("c", "2026-03-03", hn="3", an="690001")], "dtx": [], "ogtt": [], "anc": []}
    errors = {r.vn: [e["code"] for e in r.errors] for r in ppfs_gdm.check(data, ppfs_gdm.GDM, date(2026, 1, 1), date(2026, 12, 31))}
    assert errors == {"a": ["NOT_UC"], "b": ["NOT_FEMALE"], "c": ["IPD"]}


def test_services_are_listed_first_in_order():
    assert list(ppfs.SERVICES)[:2] == ["gdm", "pdm"] and ppfs.SERVICES["gdm"].codes["GDM"][1] == ppfs_gdm.GDM.rate


def test_pdm_rate_follows_the_weeks_at_the_first_visit():
    assert [ppfs_gdm.pdm_rate(w) for w in (0, 4, 5, 13, 14, 26, 27, 36, 37, 42)] == [
        Decimal(x) for x in ("8660", "8660", "7820", "6980", "6140", "4460", "3620", "2780", "1940", "1940")]


def pdm_visit(vn, day, hn="1"):
    return {**visit(vn, day, hn=hn), "icd10": "O241"}


def test_pdm_takes_the_anc_week_else_the_lmp_and_counts_hba1c():
    hba1c = [lab(v, d, "6.1") for v, d in (("a", "2026-03-01"), ("x", "2026-05-01"), ("y", "2026-07-01"))]
    data = {"visits": [pdm_visit("a", "2026-03-01"), pdm_visit("b", "2026-03-10", hn="2"), pdm_visit("c", "2026-03-20", hn="3")],
            "dtx": [lab("a", "2026-03-01")], "ogtt": [], "hba1c": hba1c + [lab("z", "2025-01-01", "6.0")],
            "anc": [{"hn": "1", "lmp": "2026-01-01"}, {"hn": "2", "lmp": "2026-01-20"}], "weeks": {"a": 10}}
    rows = {r.vn: r for r in ppfs_gdm.check(data, ppfs_gdm.PDM, date(2026, 1, 1), date(2026, 12, 31))}
    a, b, c = rows["a"], rows["b"], rows["c"]
    assert (a.code, a.amount, a.extra["weeks"], len(a.extra["hba1c"])) == ("PDM", Decimal("6980"), 10, 3)
    assert [w["code"] for w in a.warnings] == ["HBA1C_COUNT"] and not a.errors
    assert (b.amount, b.extra["weeks"], b.extra["weeks_from"]) == (Decimal("7820"), 7, "นับจาก LMP")
    assert [e["code"] for e in c.errors] == ["NO_GA"]
