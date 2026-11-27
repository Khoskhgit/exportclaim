import time
from dataclasses import replace

from fastapi.testclient import TestClient

from app import seamless_web as sm
from app.main import create_app
from app.seamless_web import Project, RepRow, SeamlessError
from app.store import Store
from factories import settings
from test_api import FakeHosxp

F = sm.F
HEAD = "".join(f'<th rowspan="6"><span>{h}</span></th>' for h in sm.HEADS)


def row(ri, rep_no, paid="9,120.00"):
    link = f'<a id="{F}dtb01:{ri}:j_idt236" href="#" aria-label="x" onclick="PrimeFaces.ab({{}});return false;" title="รายงานในรูปแบบ Excel"><img/></a>'
    amounts = ["371", "9,590.00", "352", paid, "19", "470.00"] + ["0", "0.00"] * 4 + ["2", "100.00", "1", "30.00"]
    cells = [link, "เขต 6 ระยอง", "สระแก้ว", "10866", "รพ.คลองหาด", f"DNAP70{ri}", rep_no, "31/10/2569", *amounts]
    return f'<tr data-ri="{ri}" class="ui-widget-content">' + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def test_rep_rows_and_dialog_buttons():
    table = f'<div id="{F}dtb01"><span class="ui-paginator-current">(1 - 2 of 23)</span><table><thead><tr>{HEAD}</tr></thead>' \
            f'<tbody>{row(0, "DNAP69103100120")}{row(1, "DNAP69100100119", "20.00")}</tbody></table></div>'
    total, rows = sm.rep_rows(table, Project("1", "NAP"))
    assert total == 23 and [r.rep_no for r in rows] == ["DNAP69103100120", "DNAP69100100119"]
    first = rows[0]
    assert (first.ri, first.link, first.period, first.rep_no, first.rep_date) == (
        "0", f"{F}dtb01:0:j_idt236", "DNAP700", "DNAP69103100120", "31/10/2569")
    assert first.amounts == {"claimed_items": 371, "claimed": 9590, "paid_items": 352, "paid": 9120, "denied_items": 19,
                             "denied": 470, "appeal_add": 100, "appeal_recall": 30}
    assert first.net == 9190.0 and rows[1].net == 90.0  # ชดเชย + จ่ายเพิ่ม - เรียกคืน
    # a paginator reply carries rows only, sometimes with the Thai title as "?"
    paged = row(20, "X").replace("รายงานในรูปแบบ Excel", "?????? Excel")
    assert sm.rep_rows(paged, Project("1", "NAP"), whole_table=False)[1][0].ri == "20"
    dialog = ('<tr><td><label id="a">Unite report : </label></td><td><input type="submit" name="dialogsForm:j_idt410" value="" '
              'class="btn-icon-excel" /><label id="b">10866_DNAP69103100120</label></td></tr>'
              '<tr><td><label id="c">By Period 1 : </label></td><td><input type="submit" name="dialogsForm:j_idt433" value="" '
              'class="btn-icon-excel" /><label id="d">10866_DNAP7002100002</label></td></tr>')
    assert sm.excel_buttons(dialog) == [("Unite report", "dialogsForm:j_idt410", "10866_DNAP69103100120"),
                                        ("By Period 1", "dialogsForm:j_idt433", "10866_DNAP7002100002")]
    assert sm.be_date("31/10/2569") == "2026-10-31" and sm.be_date("x") == ""


def test_changed_table_is_refused():
    try:
        sm.rep_rows("<table><tr><th rowspan=\"6\">อื่น</th></tr></table>", Project("1", "x"))
    except SeamlessError as exc:
        assert "เปลี่ยนรูปแบบ" in str(exc)
    else:
        raise AssertionError


def test_summary_page_controls():
    page = ('<form id="templateForm" action="/x"><input type="hidden" name="javax.faces.ViewState" value="v1"/>'
            '<label>โครงการ :</label><select id="templateForm:summaryForm:j_idt43_input" name="templateForm:summaryForm:j_idt43_input">'
            '<option value="">--- กรุณาเลือก ---</option><option value="1">NAP</option><option value="6">วัณโรค</option></select>'
            '<select id="templateForm:summaryForm:yearSelectId_input" name="templateForm:summaryForm:yearSelectId_input">'
            '<option value="2027">2570</option><option value="2026">2569</option></select>'
            '<button id="templateForm:summaryForm:j_idt96" name="b"><span>ค้นหา</span></button></form>')
    assert sm.projects(page) == ("templateForm:summaryForm:j_idt43", [Project("1", "NAP"), Project("6", "วัณโรค")])
    assert sm.button_id(page, "ค้นหา") == "templateForm:summaryForm:j_idt96"
    assert sm.years(page) == {2027, 2026} and sm.years("<form></form>") == set()
    action, fields, _ = sm.form(page, "templateForm")
    assert action == "/x" and fields["javax.faces.ViewState"] == "v1" and fields[F + "j_idt43_input"] == ""


class FakeSeamless:
    def __init__(self, fail_project=None):
        self.data = {"1": ["R1", "R2"], "6": ["R3"], "13": []}
        self.fail_project, self.downloads, self.searches, self.bump = fail_project, [], [], 0

    def enter(self):
        return [Project("1", "NAP"), Project("6", "วัณโรค"), Project("13", "ฝากครรภ์"), Project("28", "อื่น")]

    def reps(self, project, year):
        self.searches.append((project.id, year))
        if project.id == "28":
            raise SeamlessError("Seamless ตอบ HTTP 500")
        return [RepRow(project.id, project.name, str(i), f"l{i}", "P", no, "31/10/2569",
                       {"claimed": 12.0 + self.bump, "paid": 10.0 + self.bump, "denied": 2.0})
                for i, no in enumerate(self.data[project.id])]

    def download(self, row):
        self.downloads.append(row.rep_no)
        return f"10866_{row.rep_no}.xlsx", b"PK" + row.rep_no.encode()


def wait(app):
    for _ in range(200):
        status = app.get("/api/seamless").json()
        if not status["job"]["running"]:
            return status
        time.sleep(0.02)
    raise AssertionError("pull did not finish")


def test_pull_downloads_new_reps_and_serves_the_files(tmp_path):
    fake = FakeSeamless()
    config = replace(settings(), export_dir=tmp_path / "e", eclaim_user="u", eclaim_password="p", eclaim_totp_secret="GEZDGNBV")
    app = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3"), seamless=fake))
    assert app.post("/api/seamless", json={"year": 2560}).status_code == 400
    assert app.post("/api/seamless", json={"year": 2569}).status_code == 200
    status = wait(app)
    job = status["job"]
    assert (job["found"], job["known"], job["total"], job["imported"], job["errors"]) == (3, 0, 3, 3, 1)
    assert ("1", 2026) in fake.searches and sorted(fake.downloads) == ["R1", "R2", "R3"]
    assert any("อื่น: Seamless ตอบ HTTP 500" in line["text"] for line in job["lines"] if line["kind"] == "error")
    reps = {r["rep_no"]: r for r in status["reps"]}
    assert reps["R1"]["project"] == "NAP" and reps["R1"]["rep_date"] == "2026-10-31" and reps["R1"]["fiscal_year"] == 2569
    assert (reps["R1"]["claimed"], reps["R1"]["paid"], reps["R1"]["denied"], reps["R1"]["net"]) == (12.0, 10.0, 2.0, "10.00")
    got = app.get("/api/seamless/file/R1")
    assert got.status_code == 200 and got.content == b"PKR1" and "10866_R1.xlsx" in got.headers["content-disposition"]
    assert app.get("/api/seamless/file/nope").status_code == 404

    fake.data["6"].append("R4")
    fake.bump = 5  # an appeal changed the amounts of REPs already downloaded
    app.post("/api/seamless", json={"year": 2569})
    status = wait(app)
    again = status["job"]
    assert {r["rep_no"]: r["net"] for r in status["reps"]}["R1"] == "15.00"
    assert (again["found"], again["known"], again["total"], again["imported"]) == (4, 3, 1, 1)
    assert fake.downloads.count("R1") == 1 and fake.downloads[-1] == "R4"


def test_pull_needs_settings(tmp_path):
    config = replace(settings(), export_dir=tmp_path / "e")
    app = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3")))
    assert app.get("/api/seamless").json()["ready"] is False
    app.post("/api/seamless", json={"year": 2569})
    job = wait(app)["job"]
    assert job["errors"] == 1 and "ECLAIM_TOTP_SECRET" in job["lines"][-1]["text"]


def test_store_adds_amount_columns_to_an_old_table(tmp_path):
    import sqlite3
    path = tmp_path / "s.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE seamless_reps (rep_no TEXT PRIMARY KEY, project_id TEXT NOT NULL, project TEXT NOT NULL, "
                   "period TEXT NOT NULL, rep_date TEXT NOT NULL, net TEXT NOT NULL, fiscal_year INTEGER NOT NULL, "
                   "file TEXT NOT NULL, downloaded_at TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT '')")
        db.execute("INSERT INTO seamless_reps VALUES ('R1','1','NAP','P','2026-10-31','0.00',2569,'10866_R1.xlsx','t','')")
    store = sm.SeamlessStore(Store(path))
    store.refresh([RepRow("1", "NAP", "0", "l", "P", "R1", "31/10/2569", {"paid": 9120.0, "appeal_recall": 20.0})])
    row = store.listing()[0]
    assert (row["paid"], row["appeal_recall"], row["net"]) == (9120.0, 20.0, "9100.00")
    assert store.forget(["R1"]) == 1 and store.listing() == []


def test_rows_holding_another_reps_file_are_dropped_on_start(tmp_path):
    store = sm.SeamlessStore(Store(tmp_path / "s.sqlite3"))
    good = RepRow("9", "ดาวน์", "0", "l", "P", "DDOW69013100067", "31/01/2569", {"paid": 1.0})
    bad = RepRow("13", "ฝากครรภ์", "0", "l", "P", "DANC65093000032", "30/09/2565", {"paid": 1.0})
    store.save(good, "10866_DDOW69013100067.xlsx", b"PK", 2569, "")
    store.save(bad, "10866_DDOW69013100067.xlsx", b"PK", 2569, "")  # what the first release did
    again = sm.SeamlessStore(Store(tmp_path / "s.sqlite3"))
    assert [r["rep_no"] for r in again.listing()] == ["DDOW69013100067"]
    assert (again.folder / "10866_DDOW69013100067.xlsx").exists()


def test_pull_2570_and_a_year_the_site_does_not_offer(tmp_path):
    fake = FakeSeamless()
    config = replace(settings(), export_dir=tmp_path / "e", eclaim_user="u", eclaim_password="p", eclaim_totp_secret="GEZDGNBV")
    app = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3"), seamless=fake))
    assert app.get("/api/seamless").json()["years"][0] == 2570
    assert app.post("/api/seamless", json={"year": 2570}).status_code == 200
    wait(app)
    assert ("1", 2027) in fake.searches
    fake.years, fake.searches = {2026}, []  # the site's drop-down has no 2570 yet: pull 2569, not an error
    app.post("/api/seamless", json={"year": 2570})
    job = wait(app)["job"]
    assert ("1", 2026) in fake.searches and not any("ไม่มีปีงบ" in l.get("text", "") for l in job["lines"] if l.get("ok") is False)
    assert any("ยังไม่เปิดปีงบ 2570 ดึงปีงบ 2569 แทน" in l["text"] for l in job["lines"] if l.get("text"))
    fake.years, fake.searches = {2027, 2026}, []  # an old year the site no longer lists is still an error
    app.post("/api/seamless", json={"year": 2568})
    job = wait(app)["job"]
    assert fake.searches == [] and job["errors"] == 1 and "ไม่มีปีงบ 2568" in job["lines"][-1]["text"]
