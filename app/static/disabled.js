"use strict";
// ประกันสังคม กรณีทุพพลภาพ: imported SSO send / payment reports matched to HOSxP, and visits under the right not sent yet.
const state = { claims: [], filter: "all", text: "", pttypes: ["33"], current: null, sort: { key: "admit_date", dir: "desc" } };
const money = (v) => (v === "" || v === null || v === undefined ? "" : baht.format(Number(v)));
const dash = () => el("span", { class: "muted" }, "—");
const KIND = { OPD: "ผู้ป่วยนอก", IPD: "ผู้ป่วยใน" };
const paidOff = (c) => c.paid_amount !== "" && Number(c.paid_amount) !== Number(c.claim_amount);
const claimOff = (c) => c.claim_amount !== "" && c.right_money !== "" && Number(c.claim_amount) !== Number(c.right_money);

const STATES = {
  paid: ["จ่ายแล้ว", "b-ok"],
  paid_no_detail: ["จ่ายแล้ว (ยังไม่มีรายงานจ่าย)", "b-sent"],
  waiting: ["ส่งเบิกแล้ว", "b-warn"],
  rejected: ["ไม่อนุมัติ", "b-error"],
  unknown: ["มีแต่รายงานจ่าย", "b-sent"],
  unclaimed: ["ยังไม่ส่งเบิก", "b-c"],
};
const claimed = (c) => c.state !== "unclaimed";
const CATEGORIES = {
  all: ["ส่งเบิกแล้วทั้งหมด", claimed, "all"],
  paid: ["จ่ายแล้ว", (c) => c.state === "paid" || c.state === "paid_no_detail", "ok"],
  waiting: ["รอจ่าย", (c) => c.state === "waiting", "warn"],
  rejected: ["ไม่อนุมัติ", (c) => c.state === "rejected", "error"],
  nomatch: ["ไม่พบ visit / ไม่ใช่สิทธิทุพพลภาพ", (c) => claimed(c) && (!c.vn || !c.right_ok), "special"],
  mismatch: ["ยอดไม่ตรง", (c) => claimOff(c) || paidOff(c), "sent"],
  unclaimed: ["สิทธิทุพพลภาพ ยังไม่ส่งเบิก", (c) => c.state === "unclaimed", "error"],
};

function notes(c) {
  const list = [];
  if (claimed(c)) {
    if (!c.in_hosxp) list.push("ไม่พบเลขบัตรนี้ใน HOSxP");
    else if (!c.vn) list.push(`ไม่พบ${KIND[c.kind]}วันที่ ${toDmy(c.admit_date)}`);
    if (c.vn && !c.right_ok) list.push(`ไม่ได้ใช้สิทธิ ${state.pttypes.join(", ")}`);
  }
  if (c.visits_that_day > 1) list.push(`วันนั้นมี ${c.visits_that_day} ${c.kind === "IPD" ? "admission" : "visit"}`);
  if (claimOff(c)) list.push(`ขอเบิกต่างจากยอดสิทธิใน HOSxP ${baht.format(Number(c.claim_amount) - Number(c.right_money))}`);
  if (paidOff(c)) list.push(`จ่ายต่างจากขอเบิก ${baht.format(Number(c.paid_amount) - Number(c.claim_amount))}`);
  if (c.state === "waiting" && c.status && c.status !== "ส่งเบิกแล้ว") list.push(c.status);
  if (c.kind === "IPD" && c.discharge_date && c.discharge_date !== c.admit_date) list.push(`จำหน่าย ${toDmy(c.discharge_date)}`);
  return list;
}

function visible() {
  const q = state.text.toLowerCase();
  const test = CATEGORIES[state.filter][1];
  const list = state.claims.filter((c) => test(c) && (!q || `${c.vn} ${c.an} ${c.hn} ${c.ptname} ${c.voucher}`.toLowerCase().includes(q)));
  return sortRows(list, state.sort, ["claim_amount", "paid_amount", "right_money"]);
}

const rowId = (c) => `${c.kind}|${c.an || c.vn}|${c.cid}|${c.admit_date}|${c.claim_amount}|${c.state}`;

function render() {
  renderCards($("summary"), CATEGORIES, state.claims, state.filter, (c) => c.claim_amount || c.right_money,
    (key) => { state.filter = key; render(); }, "รายการ");
  const list = visible();
  showSort($("thead"), state.sort);
  $("rows").replaceChildren(...list.map((c) => {
    const [label, tone] = STATES[c.state] || [c.state, "b-sent"];
    const remarks = notes(c);
    return el("tr", { class: `${claimOff(c) || paidOff(c) ? "flag-row" : ""} ${state.current === rowId(c) ? "current" : ""}`.trim(), onclick: () => openDrawer(c) },
      el("td", {}, el("span", { class: `badge ${tone}` }, label)),
      el("td", { class: "mono" }, toDmy(c.admit_date)),
      el("td", {}, KIND[c.kind] || c.kind),
      el("td", { class: "mono" }, c.an ? `AN ${c.an}` : c.vn || dash()), el("td", { class: "mono" }, c.hn || dash()),
      el("td", { class: "name-cell", title: c.ptname }, c.ptname || dash(), c.pttype ? el("span", { class: "sub" }, c.pttype) : null),
      el("td", { class: "mono" }, c.cid),
      el("td", { class: "num" }, c.right_money !== "" ? money(c.right_money) : dash()),
      el("td", { class: "num" }, c.claim_amount !== "" ? money(c.claim_amount) : dash()),
      el("td", { class: "mono" }, c.claim_date ? toDmy(c.claim_date) : dash()),
      el("td", { class: "num paid" }, c.paid_amount !== "" ? money(c.paid_amount) : dash()),
      el("td", { class: "problems" }, remarks.length ? remarks.join(" · ") : dash()));
  }));
  $("empty").hidden = list.length > 0;
  if (!list.length && state.claims.length) $("empty").replaceChildren(...emptyState("ไม่มีรายการตามตัวกรองนี้"));
  $("result-count").textContent = state.claims.length ? `แสดง ${count.format(list.length)} จาก ${count.format(state.claims.length)} รายการ` : "";
}

// ------------------------------------------------------------- drawer
function info(pairs) {
  return el("dl", { class: "info-grid" }, ...pairs.map(([k, v]) => el("div", {}, el("dt", {}, k), el("dd", {}, v || "—"))));
}
function table(headers, rows) {
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", { class: h.num ? "num" : "" }, h.label || h)))), el("tbody", {}, ...rows)));
}
function closeDrawer() { $("drawer").hidden = true; $("drawer-backdrop").hidden = true; state.current = null; render(); }

async function openDrawer(c) {
  const id = rowId(c);
  state.current = id; render();
  $("drawer").hidden = false; $("drawer-backdrop").hidden = false;
  $("drawer-title").textContent = c.ptname || (c.vn ? `VN ${c.vn}` : "รายการทุพพลภาพ");
  $("drawer-sub").textContent = [c.an && `AN ${c.an}`, c.vn && `VN ${c.vn}`, c.hn && `HN ${c.hn}`, `${KIND[c.kind]} ${toDmy(c.admit_date)}`].filter(Boolean).join(" · ");
  const [label, tone] = STATES[c.state] || [c.state, "b-sent"];
  const summary = [
    el("p", {}, el("span", { class: `badge ${tone}` }, label)),
    info([["เลขบัตร", c.cid], ["สิทธิ (HOSxP)", c.pttype], ["ประเภท", KIND[c.kind]],
      ["Admit / Discharge", `${toDmy(c.admit_date)}${c.discharge_date ? ` – ${toDmy(c.discharge_date)}` : ""}`],
      ["ค่าใช้จ่ายทั้งหมด (HOSxP)", c.income !== "" ? `${money(c.income)} บาท` : ""], ["ยอดสิทธิ (HOSxP)", c.right_money !== "" ? `${money(c.right_money)} บาท` : ""],
      ["วันที่ส่งเบิก", c.claim_date && toDmy(c.claim_date)], ["ยอดขอเบิก", c.claim_amount !== "" ? `${money(c.claim_amount)} บาท` : ""],
      ["ยอดจ่าย", c.paid_amount !== "" ? `${money(c.paid_amount)} บาท` : ""], ["เลขที่ใบสำคัญรับเงิน", c.voucher],
      ["วันที่โอน", c.transfer_date && toDmy(c.transfer_date)], ["สถานะ (สปส.)", c.status]]),
  ];
  const remarks = notes(c);
  if (remarks.length) summary.push(el("ul", { class: "issues" }, ...remarks.map((r) => el("li", { class: "issue warn" }, el("div", { class: "issue-title" }, r)))));
  if (c.state === "unclaimed") summary.push(el("p", { class: "hint" }, "ยังไม่พบในรายงานการส่งเบิกที่นำเข้า ส่งเบิกในระบบ สปส. แล้วนำเข้ารายงานการส่งเบิกใหม่"));
  const body = $("drawer-body");
  if (!c.vn || c.kind === "IPD") {
    body.replaceChildren(...summary, c.vn ? null : el("p", { class: "hint" }, "ยังไม่พบ visit ใน HOSxP ตรวจเลขบัตรและวันที่รับบริการ"));
    return;
  }
  body.replaceChildren(...summary, loadingBlock());
  try {
    const visit = await api("/api/detail", { vn: c.vn });
    if (state.current !== id) return;
    const total = visit.items.reduce((sum, i) => sum + Number(i.sum_price || 0), 0);
    const items = visit.items.length
      ? table(["หมวด", "รายการ", { label: "จำนวน", num: true }, { label: "ราคา/หน่วย", num: true }, { label: "รวม", num: true }], visit.items.map((i) => el("tr", {},
        el("td", {}, i.group), el("td", {}, i.name), el("td", { class: "num" }, i.qty), el("td", { class: "num" }, money(i.unitprice)), el("td", { class: "num" }, money(i.sum_price)))))
      : el("p", { class: "muted" }, "ไม่มีรายการค่าใช้จ่าย");
    const dx = visit.dx.length
      ? table(["ประเภท", "รหัส", "ชื่อโรค"], visit.dx.map((d) => el("tr", {}, el("td", {}, d.type), el("td", { class: "mono" }, d.code), el("td", {}, d.name))))
      : el("p", { class: "muted" }, "ไม่มีข้อมูลการวินิจฉัย");
    body.replaceChildren(...tabs([
      { id: "claim", label: "รายการเบิก", content: summary },
      { id: "cost", label: "ค่าใช้จ่าย", badge: visit.items.length, content: [el("div", { class: "amount-bar" }, el("div", {}, el("span", {}, "รวมทั้ง visit "), el("b", {}, money(total)))), items] },
      { id: "dx", label: "วินิจฉัย", badge: visit.dx.length, content: [dx] },
    ]));
  } catch (err) {
    if (state.current === id) body.replaceChildren(...summary, el("p", { class: "danger-text" }, err.message));
  }
}

function renderHistory(uploads) {
  $("history").replaceChildren(uploads.length ? el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...["เวลา", "ไฟล์", "ชนิด", "ช่วงวันที่", "รายการ", "ยอดรวม", "ผู้นำเข้า"].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...uploads.map((u) => el("tr", {},
      el("td", {}, thaiDateTime(u.at)), el("td", {}, u.name), el("td", {}, u.kind === "payment" ? "รายงานการจ่าย" : "รายงานการส่งเบิก"),
      el("td", {}, `${toDmy(u.period_start)} – ${toDmy(u.period_end)}`), el("td", { class: "num" }, String(u.row_count)),
      el("td", { class: "num" }, money(u.total)), el("td", {}, u.created_by)))))) : el("p", { class: "hint" }, "ยังไม่มีการนำเข้า"));
}

async function load() {
  const form = $("search");
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  showLoading("กำลังโหลดรายการ…");
  try {
    const data = await api("/api/disabled/list", { start: form.elements.start.value, end: form.elements.end.value });
    state.claims = data.claims;
    state.pttypes = data.pttypes;
    if (data.db_error) notice(data.db_error, "warn");
    renderHistory(data.uploads);
  } catch (err) { notice(err.message); } finally { button.disabled = false; }
  render();
}

async function importFiles(files) {
  const log = $("import-log");
  for (const file of files) {
    const line = el("li", { class: "busy" }, `${file.name} · กำลังนำเข้า…`);
    log.prepend(line);
    try {
      const response = await fetch(`/api/disabled/import?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `ผิดพลาด (${response.status})`);
      line.className = "ok";
      line.replaceChildren(el("strong", {}, `✓ ${file.name}`),
        ` · ${data.kind} ${toDmy(data.period_start)} – ${toDmy(data.period_end)} · ${data.rows} รายการ ${money(data.total)} บาท (ใหม่ ${data.added}, อัปเดต ${data.updated})`,
        data.again ? el("span", { class: "sub" }, data.again) : null);
    } catch (err) {
      line.className = "error";
      line.replaceChildren(el("strong", {}, `✕ ${file.name}`), ` · ${err.message}`);
    }
  }
  $("files").value = "";
  await load();
}

function setupDrop() {
  const drop = $("drop");
  $("files").addEventListener("change", (e) => importFiles([...e.target.files]));
  ["dragenter", "dragover"].forEach((type) => drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((type) => drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => importFiles([...e.dataTransfer.files]));
}

async function connection() {
  try {
    const status = await api("/api/status");
    if (status.database) setConnection(`HOSxP · ${status.db_host}`, true);
    else setConnection(status.db_error ? `เชื่อมต่อไม่ได้ (${status.db_host})` : "ยังไม่ได้ตั้งค่าฐานข้อมูล", false);
  } catch (err) { setConnection("เชื่อมต่อไม่ได้", false); }
}

$("drawer-close").onclick = closeDrawer;
$("drawer-backdrop").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeDrawer(); });
$("search").addEventListener("submit", (e) => { e.preventDefault(); clearNotices(); load(); });
$("show-all").onclick = () => { $("search").elements.start.value = ""; $("search").elements.end.value = ""; load(); };
$("fiscal").onclick = () => {
  const today = new Date();
  const year = today.getMonth() >= 9 ? today.getFullYear() : today.getFullYear() - 1;
  $("search").elements.start.value = `${year}-10-01`;
  $("search").elements.end.value = isoDate(today);
};
$("filter").addEventListener("input", (e) => { state.text = e.target.value; render(); });
bindSort($("thead"), state.sort, render);
setupPresets($("search"));
$("fiscal").click();
setupDrop();
load();
connection();
