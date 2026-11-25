"use strict";
const state = { rows: [], hospitals: [], filter: "all", text: "", period: null, current: null, sort: { key: "", dir: "asc" } };
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
  $("rows").replaceChildren(...list.map((r) => el("tr", { class: state.current === r.vn ? "current" : "", onclick: () => openDrawer(r) },
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

// ------------------------------------------------------------- drawer
// The visit's CC, diagnoses, procedures and costs, read with the same query as the 18 แฟ้ม page.
function table(headers, rows, foot) {
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", { class: h.num ? "num" : "" }, h.label || h)))),
    el("tbody", {}, ...rows), foot || null));
}
const none = (message) => el("p", { class: "muted" }, message);

async function openDrawer(r) {
  state.current = r.vn; render();
  $("drawer").hidden = false; $("drawer-backdrop").hidden = false;
  $("drawer-title").textContent = r.ptname || r.vn;
  $("drawer-sub").textContent = `VN ${r.vn} · HN ${r.hn} · ${toDmy(r.vstdate)}`;
  const body = $("drawer-body");
  body.replaceChildren(loadingBlock());
  const labs = state.rows.filter((x) => x.vn === r.vn);
  const labCodes = new Set(labs.map((x) => x.icode));
  const labTotal = labs.reduce((s, x) => s + Number(x.sum_price), 0);
  try {
    const data = await api("/api/nhso18/detail", { key: r.vn, kind: "OPD" });
    if (state.current !== r.vn) return;
    const summary = [
      el("dl", { class: "info-grid" }, ...[["เลขบัตร", r.cid], ["สิทธิ", r.pttype], ["สถานพยาบาลหลัก", r.hospmain_name], ["PDx", r.pdx],
        ["วันที่รับบริการ", toDmy(r.vstdate)], ["LAB ส่งตรวจนอก", `${count.format(labs.length)} รายการ · ${baht.format(labTotal)} บาท`]]
        .map(([k, v]) => el("div", {}, el("dt", {}, k), el("dd", {}, v || "—")))),
      el("h3", {}, "LAB ส่งตรวจนอก (เรียกเก็บ)"),
      table(["icode", "รายการ LAB", { label: "จำนวน", num: true }, { label: "ราคา/หน่วย", num: true }, { label: "รวม", num: true }],
        labs.map((x) => el("tr", {}, el("td", { class: "mono" }, x.icode), el("td", {}, x.lab_name),
          el("td", { class: "num" }, count.format(Number(x.qty))), el("td", { class: "num" }, baht.format(Number(x.unitcost))),
          el("td", { class: "num" }, baht.format(Number(x.sum_price))))),
        el("tfoot", {}, el("tr", {}, el("th", { colSpan: 4 }, "รวม"), el("th", { class: "num" }, baht.format(labTotal))))),
    ];
    const procs = data.procedures || [];
    const dx = [el("h3", {}, "CC (อาการสำคัญ)"), data.cc ? el("p", { class: "cc-text" }, data.cc) : none("ไม่มีข้อมูล CC"),
      el("h3", {}, "การวินิจฉัย"),
      data.dx.length ? table(["ประเภท", "รหัส", "ชื่อโรค"], data.dx.map((d) => el("tr", {}, el("td", {}, d.type), el("td", { class: "mono" }, d.code), el("td", {}, d.name)))) : none("ไม่มีข้อมูล"),
      el("h3", {}, "หัตถการ (OOP)"),
      procs.length ? table(["ที่มา", "รหัส", "ชื่อหัตถการ", "วันที่", "ผู้ทำ (ว.)"], procs.map((p) => el("tr", {},
        el("td", {}, p.type), el("td", { class: "mono" }, p.code), el("td", {}, p.name || "—"),
        el("td", { class: "mono" }, p.start ? `${toDmy(p.start.slice(0, 10))}${p.start.slice(10)}` : "—"), el("td", { class: "mono" }, p.dropid || "—"))))
        : none("ไม่มีหัตถการ")];
    const itemTotal = data.items.reduce((s, i) => s + Number(i.amount || 0), 0);
    const costs = [el("p", { class: "muted-note" }, "ค่าใช้จ่ายทั้งหมดของ visit นี้ใน HOSxP แถวสีเหลืองคือ LAB ส่งตรวจนอกที่อยู่ในรายงานนี้"),
      table(["หมวด", "รายการ", "ADP", { label: "จำนวน", num: true }, { label: "รวม", num: true }], data.items.map((i) =>
        el("tr", { class: labCodes.has(i.icode) ? "outlab" : "" }, el("td", {}, i.billmaud, i.billmaud_name ? el("span", { class: "sub" }, i.billmaud_name) : null),
          el("td", {}, i.name), el("td", { class: "mono" }, i.adp), el("td", { class: "num" }, i.qty), el("td", { class: "num" }, baht.format(Number(i.amount))))),
        el("tfoot", {}, el("tr", {}, el("th", { colSpan: 4 }, "รวมทั้งหมด"), el("th", { class: "num" }, baht.format(itemTotal)))))];
    body.replaceChildren(...tabs([
      { id: "check", label: "สรุป", badge: labs.length, content: summary },
      { id: "dx", label: "การวินิจฉัย", badge: data.dx.length + procs.length, content: dx },
      { id: "cost", label: "ค่าใช้จ่าย", badge: data.items.length, content: costs },
    ]));
  } catch (err) { body.replaceChildren(el("p", {}, err.message)); }
}
function closeDrawer() { $("drawer").hidden = true; $("drawer-backdrop").hidden = true; state.current = null; render(); }

function period() {
  const form = $("search");
  return { start: form.elements.start.value, end: form.elements.end.value };
}

async function load() {
  const body = period();
  if (!body.start || !body.end) { notice("กรุณาเลือกช่วงวันที่"); return; }
  if (dbBlocked()) return;
  const button = $("search").querySelector("button[type=submit]");
  button.disabled = true;
  showLoading("กำลังค้นหา…");
  try {
    const data = await api("/api/labwalkin", body);
    Object.assign(state, { rows: data.rows, hospitals: data.hospitals, filter: "all", period: body });
    render();
  } catch (err) { if (DB_ERROR.test(err.message)) $("empty").replaceChildren(...searchFailed(err)); else notice(err.message); } finally { button.disabled = false; }
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
$("drawer-close").onclick = closeDrawer;
$("drawer-backdrop").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeDrawer(); });
$("filter").addEventListener("input", (e) => { state.text = e.target.value; render(); });
bindSort($("thead"), state.sort, render);
setupPresets($("search"));
$("search").querySelector('[data-preset="last"]').click();
render();
connection();
