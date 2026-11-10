"use strict";
const state = { claims: [], filter: "all", text: "", sort: { key: "service_date", dir: "desc" } };
const money = (v) => (v === "" || v === null || v === undefined ? "" : baht.format(Number(v)));
const dash = () => el("span", { class: "muted" }, "—");
const mismatch = (c) => c.paid_amount !== "" && Number(c.paid_amount) !== Number(c.claim_amount);

const STATES = {
  paid: ["จ่ายแล้ว", "b-ok"],
  paid_no_detail: ["จ่ายแล้ว (ยังไม่มีรายงานจ่าย)", "b-sent"],
  waiting: ["รอจ่าย", "b-warn"],
  rejected: ["ไม่อนุมัติ", "b-error"],
  unknown: ["มีแต่รายงานจ่าย", "b-sent"],
};
const CATEGORIES = {
  all: ["ทั้งหมด", () => true, "all"],
  paid: ["จ่ายแล้ว", (c) => c.state === "paid" || c.state === "paid_no_detail", "ok"],
  waiting: ["รอจ่าย", (c) => c.state === "waiting", "warn"],
  rejected: ["ไม่อนุมัติ", (c) => c.state === "rejected", "error"],
  nomatch: ["ไม่พบ visit ใน HOSxP", (c) => !c.vn, "special"],
  mismatch: ["ยอดจ่ายไม่ตรง", mismatch, "sent"],
};

function notes(c) {
  const list = [];
  if (!c.in_hosxp) list.push("ไม่พบเลขบัตรนี้ใน HOSxP");
  else if (!c.vn) list.push("ไม่พบ visit วันที่ใช้สิทธิ");
  else if (!c.has_dental) list.push("visit นี้ไม่มีบันทึกทันตกรรม");
  if (c.visits_that_day > 1) list.push(`วันนั้นมี ${c.visits_that_day} visit`);
  if (mismatch(c)) list.push(`จ่ายต่างจากขอเบิก ${baht.format(Number(c.paid_amount) - Number(c.claim_amount))}`);
  if (c.state === "waiting" && c.status !== "รอจ่าย") list.push(c.status);
  return list;
}

function visible() {
  const q = state.text.toLowerCase();
  const test = CATEGORIES[state.filter][1];
  const list = state.claims.filter((c) => test(c) && (!q || `${c.vn} ${c.hn} ${c.ptname} ${c.voucher}`.toLowerCase().includes(q)));
  return sortRows(list, state.sort, ["claim_amount", "paid_amount"]);
}

function render() {
  renderCards($("summary"), CATEGORIES, state.claims, state.filter, (c) => c.claim_amount,
    (key) => { state.filter = key; render(); });
  const list = visible();
  showSort($("thead"), state.sort);
  $("rows").replaceChildren(...list.map((c) => {
    const [label, tone] = STATES[c.state] || [c.state, "b-sent"];
    const remarks = notes(c);
    return el("tr", { class: mismatch(c) ? "flag-row" : "" },
      el("td", {}, el("span", { class: `badge ${tone}` }, label)),
      el("td", { class: "mono" }, toDmy(c.service_date)),
      el("td", { class: "mono" }, c.vn || dash()), el("td", { class: "mono" }, c.hn || dash()),
      el("td", { class: "name-cell", title: c.ptname }, c.ptname || dash(), c.pttype ? el("span", { class: "sub" }, c.pttype) : null),
      el("td", { class: "mono" }, c.cid), el("td", {}, c.service_type),
      el("td", { class: "num" }, money(c.claim_amount)),
      el("td", { class: "num paid" }, c.paid_amount !== "" ? money(c.paid_amount) : dash()),
      el("td", { class: "mono doc" }, c.voucher || dash()), el("td", { class: "mono" }, c.transfer_date ? toDmy(c.transfer_date) : dash()),
      el("td", { class: "problems" }, remarks.length ? remarks.join(" · ") : dash()));
  }));
  $("empty").hidden = list.length > 0;
  if (!list.length && state.claims.length) $("empty").replaceChildren(...emptyState("ไม่มีรายการตามตัวกรองนี้"));
  $("result-count").textContent = state.claims.length ? `แสดง ${count.format(list.length)} จาก ${count.format(state.claims.length)} รายการ` : "";
}

function renderHistory(uploads) {
  $("history").replaceChildren(uploads.length ? el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...["เวลา", "ไฟล์", "ชนิด", "ช่วงวันที่", "รายการ", "ยอดรวม", "ผู้นำเข้า"].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...uploads.map((u) => el("tr", {},
      el("td", {}, u.at.replace("T", " ")), el("td", {}, u.name), el("td", {}, u.kind === "payment" ? "รายงานการจ่าย" : "รายงานการส่งเบิก"),
      el("td", {}, `${toDmy(u.period_start)} – ${toDmy(u.period_end)}`), el("td", { class: "num" }, String(u.row_count)),
      el("td", { class: "num" }, money(u.total)), el("td", {}, u.created_by)))))) : el("p", { class: "hint" }, "ยังไม่มีการนำเข้า"));
}

async function load() {
  const form = $("search");
  try {
    const data = await api("/api/dental/list", { start: form.elements.start.value, end: form.elements.end.value });
    state.claims = data.claims;
    if (data.db_error) notice(data.db_error, "warn");
    renderHistory(data.uploads);
  } catch (err) { notice(err.message); }
  render();
}

async function importFiles(files) {
  const log = $("import-log");
  for (const file of files) {
    const line = el("li", { class: "busy" }, `${file.name} · กำลังนำเข้า…`);
    log.prepend(line);
    try {
      const response = await fetch(`/api/dental/import?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
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

$("search").addEventListener("submit", (e) => { e.preventDefault(); clearNotices(); load(); });
$("show-all").onclick = () => { $("search").elements.start.value = ""; $("search").elements.end.value = ""; load(); };
$("filter").addEventListener("input", (e) => { state.text = e.target.value; render(); });
bindSort($("thead"), state.sort, render);
setupPresets($("search"));
setupDrop();
load();
connection();
