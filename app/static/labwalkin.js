"use strict";
const state = { rows: [], hospitals: [], filter: "all", text: "", period: null, sort: { key: "", dir: "asc" } };
const NUMERIC = ["qty", "unitcost", "sum_price"];

function categories() {
  const cats = { all: ["ทุกสถานพยาบาลหลัก", () => true, "all"] };
  // Keys get an "h" prefix: number-like keys would sort ahead of "all".
  for (const h of state.hospitals) cats[`h${h.hospmain}`] = [h.hospmain_name, (r) => r.hospmain === h.hospmain, "sent"];
  return cats;
}

function visible() {
  const q = state.text.toLowerCase();
  const list = state.rows.filter((r) => (state.filter === "all" || `h${r.hospmain}` === state.filter)
    && (!q || `${r.vn} ${r.hn} ${r.ptname} ${r.lab_name}`.toLowerCase().includes(q)));
  return sortRows(list, state.sort, NUMERIC);
}

function render() {
  renderCards($("summary"), categories(), state.rows, state.filter, (r) => r.sum_price,
    (key) => { state.filter = key; render(); }, "รายการ");
  const list = visible();
  showSort($("thead"), state.sort);
  $("rows").replaceChildren(...list.map((r) => el("tr", {},
    el("td", { class: "mono" }, toDmy(r.vstdate)), el("td", { class: "mono" }, r.vn), el("td", { class: "mono" }, r.hn),
    el("td", { class: "name-cell", title: r.ptname }, r.ptname), el("td", { class: "mono" }, r.cid),
    el("td", {}, r.hospmain_name), el("td", {}, r.lab_name, el("span", { class: "sub" }, r.icode)),
    el("td", { class: "num" }, count.format(Number(r.qty))), el("td", { class: "num" }, baht.format(Number(r.unitcost))),
    el("td", { class: "num" }, baht.format(Number(r.sum_price))))));
  const total = list.reduce((sum, r) => sum + Number(r.sum_price), 0);
  $("empty").hidden = list.length > 0;
  if (!list.length && state.period) $("empty").replaceChildren(...emptyState(state.rows.length ? "ไม่มีรายการตามตัวกรองนี้" : "ไม่พบรายการในช่วงวันที่นี้"));
  $("result-count").textContent = state.period
    ? `แสดง ${count.format(list.length)} จาก ${count.format(state.rows.length)} รายการ · รวม ${baht.format(total)} บาท` : "";
  $("excel").disabled = !state.period;
}

function period() {
  const form = $("search");
  return { start: form.elements.start.value, end: form.elements.end.value };
}

async function load() {
  const body = period();
  if (!body.start || !body.end) { notice("กรุณาเลือกช่วงวันที่"); return; }
  const button = $("search").querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const data = await api("/api/labwalkin", body);
    Object.assign(state, { rows: data.rows, hospitals: data.hospitals, filter: "all", period: body });
    render();
  } catch (err) { notice(err.message); } finally { button.disabled = false; }
}

async function exportExcel() {
  const button = $("excel");
  button.disabled = true;
  try {
    const response = await fetch("/api/labwalkin/excel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state.period) });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      if (response.status === 401 && data.login) { window.location.assign("/login?error=expired"); return; }
      throw new Error(data.detail || `ผิดพลาด (${response.status})`);
    }
    const blob = await response.blob();
    const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "LAB_WalkIn.xlsx";
    const link = el("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (err) { notice(err.message); } finally { button.disabled = !state.period; }
}

async function connection() {
  try {
    const status = await api("/api/status");
    if (status.database) setConnection(`HOSxP · ${status.db_host}`, true);
    else setConnection(status.db_error ? `เชื่อมต่อไม่ได้ (${status.db_host})` : "ยังไม่ได้ตั้งค่าฐานข้อมูล", false);
  } catch (err) { setConnection("เชื่อมต่อไม่ได้", false); }
}

$("search").addEventListener("submit", (e) => { e.preventDefault(); clearNotices(); load(); });
$("excel").onclick = exportExcel;
$("filter").addEventListener("input", (e) => { state.text = e.target.value; render(); });
bindSort($("thead"), state.sort, render);
setupPresets($("search"));
$("search").querySelector('[data-preset="last"]').click();
render();
connection();
