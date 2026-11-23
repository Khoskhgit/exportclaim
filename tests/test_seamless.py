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
HEAD = "".join(f'<th rowspan="6"><span>{h}</span></th>' for h in sm.HEADS + ["ชดเชยสุทธิ"])


def row(ri, rep_no, net="1,234.50"):
    link = f'<a id="{F}dtb01:{ri}:j_idt236" href="#" aria-label="x" onclick="PrimeFaces.ab({{}});return false;" title="รายงานในรูปแบบ Excel"><img/></a>'
    cells = [link, "เขต 6 ระยอง", "สระแก้ว", "10866", "รพ.คลองหาด", f"DNAP70{ri}", rep_no, "31/10/2569", "1", "0", net]
    return f'<tr data-ri="{ri}" class="ui-widget-content">' + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def test_rep_rows_and_dialog_buttons():
    table = f'<div id="{F}dtb01"><span class="ui-paginator-current">(1 - 2 of 23)</span><table><thead><tr>{HEAD}</tr></thead>' \
            f'<tbody>{row(0, "DNAP69103100120")}{row(1, "DNAP69100100119", "0.00")}</tbody></table></div>'
    total, rows = sm.rep_rows(table, Project("1", "NAP"))
    assert total == 23 and [r.rep_no for r in rows] == ["DNAP69103100120", "DNAP69100100119"]
    assert rows[0] == RepRow("1", "NAP", "0", f"{F}dtb01:0:j_idt236", "DNAP700", "DNAP69103100120", "31/10/2569", "1,234.50")
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
            '<button id="templateForm:summaryForm:j_idt96" name="b"><span>ค้นหา</span></button></form>')
    assert sm.projects(page) == ("templateForm:summaryForm:j_idt43", [Project("1", "NAP"), Project("6", "วัณโรค")])
    assert sm.button_id(page, "ค้นหา") == "templateForm:summaryForm:j_idt96"
    action, fields, _ = sm.form(page, "templateForm")
    assert action == "/x" and fields["javax.faces.ViewState"] == "v1" and fields[F + "j_idt43_input"] == ""


class FakeSeamless:
    def __init__(self, fail_project=None):
        self.data = {"1": ["R1", "R2"], "6": ["R3"], "13": []}
        self.fail_project, self.downloads, self.searches = fail_project, [], []

    def enter(self):
        return [Project("1", "NAP"), Project("6", "วัณโรค"), Project("13", "ฝากครรภ์"), Project("28", "อื่น")]

    def reps(self, project, year):
        self.searches.append((project.id, year))
        if project.id == "28":
            raise SeamlessError("Seamless ตอบ HTTP 500")
        return [RepRow(project.id, project.name, str(i), f"l{i}", "P", no, "31/10/2569", "10.00")
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
    got = app.get("/api/seamless/file/R1")
    assert got.status_code == 200 and got.content == b"PKR1" and "10866_R1.xlsx" in got.headers["content-disposition"]
    assert app.get("/api/seamless/file/nope").status_code == 404

    fake.data["6"].append("R4")
    app.post("/api/seamless", json={"year": 2569})
    again = wait(app)["job"]
    assert (again["found"], again["known"], again["total"], again["imported"]) == (4, 3, 1, 1)
    assert fake.downloads.count("R1") == 1 and fake.downloads[-1] == "R4"


def test_pull_needs_settings(tmp_path):
    config = replace(settings(), export_dir=tmp_path / "e")
    app = TestClient(create_app(config, FakeHosxp(), Store(tmp_path / "s.sqlite3")))
    assert app.get("/api/seamless").json()["ready"] is False
    app.post("/api/seamless", json={"year": 2569})
    job = wait(app)["job"]
    assert job["errors"] == 1 and "ECLAIM_TOTP_SECRET" in job["lines"][-1]["text"]
